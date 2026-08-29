import re

with open("frontend/app.js", "r") as f:
    content = f.read()

# Replace addSystemLog function
new_logger = """
    class ActionLogger {
        constructor(actionName) {
            this.actionName = actionName;
            this.startTime = new Date();
            this.endTime = null;
            this.logDiv = document.createElement("div");
            this.logDiv.className = "log-entry";
            this.updateStatus("⏳ 发起请求...");
            
            const logsContainer = document.getElementById("system-logs");
            if (logsContainer) {
                logsContainer.appendChild(this.logDiv);
                logsContainer.scrollTop = logsContainer.scrollHeight;
            }
        }
        
        updateStatus(statusText, isError = false) {
            if (isError) this.logDiv.classList.add("error");
            const startStr = this.startTime.toLocaleTimeString();
            this.logDiv.innerHTML = `<strong>[功能: ${this.actionName}]</strong><br>开始时间: ${startStr}<br>状态: ${statusText}`;
            this._scrollToBottom();
        }
        
        finish(resultText, isError = false) {
            this.endTime = new Date();
            if (isError) {
                this.logDiv.classList.add("error");
                this.logDiv.classList.remove("success");
            } else {
                this.logDiv.classList.add("success");
            }
            
            const startStr = this.startTime.toLocaleTimeString();
            const endStr = this.endTime.toLocaleTimeString();
            const duration = ((this.endTime - this.startTime) / 1000).toFixed(2);
            
            this.logDiv.innerHTML = `<strong>[功能: ${this.actionName}]</strong><br>开始时间: ${startStr} | 结束时间: ${endStr} (耗时 ${duration}s)<br>结果: ${resultText}`;
            this._scrollToBottom();
        }
        
        _scrollToBottom() {
            const logsContainer = document.getElementById("system-logs");
            if (logsContainer) logsContainer.scrollTop = logsContainer.scrollHeight;
        }
    }

    function addSystemLog(message, isError = false) {
        const logsContainer = document.getElementById("system-logs");
        if (!logsContainer) return;
        const logDiv = document.createElement("div");
        logDiv.className = `log-entry ${isError ? "error" : ""}`;
        
        const timeString = new Date().toLocaleTimeString();
        logDiv.innerHTML = `[${timeString}] ${message}`;
        
        logsContainer.appendChild(logDiv);
        logsContainer.scrollTop = logsContainer.scrollHeight;
    }
"""

content = re.sub(
    r"function addSystemLog\(message, isError = false\) \{.*?\n    \}", 
    new_logger.strip(), 
    content, 
    flags=re.DOTALL
)

# TTS
tts_logic = """
    btnTts.addEventListener("click", async () => {
        const text = document.getElementById("tts-text").value.trim();
        if (!text) {
            addSystemLog("请输入需要转为语音的文字", true);
            return;
        }

        const logger = new ActionLogger("文字转语音 (TTS)");
        btnTts.disabled = true;
        btnTts.innerText = "音频生成中...";

        try {
            logger.updateStatus("正在调用后端接口...");
            const response = await fetch(`${API_BASE}/tts`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text }),
            });
            if (!response.ok) {
                const errorBody = await response.json().catch(() => ({}));
                throw new Error(errorBody.detail || `HTTP ${response.status}`);
            }

            const audioBlob = await response.blob();
            if (currentTtsAudioUrl) URL.revokeObjectURL(currentTtsAudioUrl);
            currentTtsAudioUrl = URL.createObjectURL(audioBlob);
            ttsAudio.src = currentTtsAudioUrl;
            ttsAudio.style.display = "block";
            await ttsAudio.play();
            logger.finish("✅ TTS 音频生成成功并已播放。");
        } catch (error) {
            logger.finish(`❌ TTS 请求失败: ${error.message}`, true);
        } finally {
            btnTts.disabled = false;
            btnTts.innerText = "生成语音并播放";
        }
    });
"""
content = re.sub(
    r'btnTts\.addEventListener\("click", async \(\) => \{.*?\n    \}\);',
    tts_logic.strip(),
    content,
    flags=re.DOTALL
)


# STT Upload
stt_upload_logic = """
    btnUpload.addEventListener("click", async () => {
        const fileInput = document.getElementById("stt-file-input");
        if (fileInput.files.length === 0) {
            addSystemLog("请先选择要识别的音频文件", true);
            return;
        }

        const logger = new ActionLogger("语音转文字 (文件上传)");
        btnUpload.disabled = true;
        btnUpload.innerText = "云端识别中...";
        resultUpload.innerText = "正在上传并等待 Gemini 处理...";
        resultUpload.style.color = "#ff9800";

        const formData = new FormData();
        formData.append("file", fileInput.files[0]);

        try {
            logger.updateStatus("正在上传音频至云端...");
            const response = await fetch(`${API_BASE}/stt/upload`, {
                method: "POST",
                body: formData,
            });
            const responseBody = await response.json().catch(() => ({}));
            if (!response.ok || responseBody.status !== "success") {
                throw new Error(responseBody.detail || "语音识别请求失败");
            }
            resultUpload.innerText = responseBody.data.text;
            resultUpload.style.color = "#34a853";
            logger.finish("✅ 文件上传识别成功。");
        } catch (error) {
            resultUpload.innerText = `失败: ${error.message}`;
            resultUpload.style.color = "red";
            logger.finish(`❌ 失败: ${error.message}`, true);
        } finally {
            btnUpload.disabled = false;
            btnUpload.innerText = "上传并识别";
        }
    });
"""
content = re.sub(
    r'btnUpload\.addEventListener\("click", async \(\) => \{.*?\n    \}\);',
    stt_upload_logic.strip(),
    content,
    flags=re.DOTALL
)

# STT Realtime (we will just modify the start and stop handlers)
# To keep track of the logger, we need a global or scoped variable
ws_setup = """
    let ws = null;
    let localStream = null;
    let pcmRecorder = null;
    let cleanupPromise = null;
    let realtimeLogger = null;
"""
content = re.sub(
    r'let ws = null;\n    let localStream = null;\n    let pcmRecorder = null;\n    let cleanupPromise = null;',
    ws_setup.strip(),
    content,
    flags=re.DOTALL
)

btn_start_logic = """
    btnStart.addEventListener("click", async () => {
        btnStart.disabled = true;
        resultRealtime.innerText = "正在申请麦克风并连接服务...";
        resultRealtime.style.color = "#4285f4";
        
        realtimeLogger = new ActionLogger("实时语音转文字 (WebSocket)");
        realtimeLogger.updateStatus("申请麦克风权限及建连中...");

        try {
            localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const socket = new WebSocket(`${WS_BASE}/stt/stream`);
            socket.binaryType = "arraybuffer";
            ws = socket;

            socket.onopen = async () => {
                try {
                    pcmRecorder = new PcmStreamRecorder(localStream, (pcmChunk) => {
                        if (socket.readyState === WebSocket.OPEN) socket.send(pcmChunk);
                    });
                    const sampleRate = await pcmRecorder.initialize();
                    socket.send(JSON.stringify({ action: "start", sample_rate: sampleRate }));
                    pcmRecorder.start();
                    resultRealtime.innerText = "连接成功，请开始说话...";
                    btnStop.disabled = false;
                    realtimeLogger.updateStatus("✅ 连接成功，等待识别结果...");
                } catch (error) {
                    resultRealtime.innerText = `录音初始化失败: ${error.message}`;
                    resultRealtime.style.color = "red";
                    realtimeLogger.finish(`录音初始化失败: ${error.message}`, true);
                    await stopAudioCapture();
                    socket.close();
                }
            };

            socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.error) {
                        resultRealtime.innerText = data.error;
                        resultRealtime.style.color = "red";
                        realtimeLogger.updateStatus(`❌ 报错: ${data.error}`, true);
                        return;
                    }
                    if (data.text) {
                        resultRealtime.innerText = data.text;
                        resultRealtime.style.color = data.is_final ? "#34a853" : "#ff9800";
                        if (data.is_final) {
                            realtimeLogger.finish(`✅ 最终识别结果: ${data.text}`);
                        } else {
                            realtimeLogger.updateStatus(`收到部分识别结果...`);
                        }
                    } else if (data.status) {
                        resultRealtime.innerText = data.status;
                        resultRealtime.style.color = "#4285f4";
                        realtimeLogger.updateStatus(`状态更新: ${data.status}`);
                    }
                    if (data.is_final && socket.readyState === WebSocket.OPEN) socket.close();
                } catch (error) {
                    resultRealtime.innerText = "服务端返回了无法解析的消息";
                    resultRealtime.style.color = "red";
                }
            };

            socket.onerror = () => {
                resultRealtime.innerText = "WebSocket 出现连接错误";
                resultRealtime.style.color = "red";
                realtimeLogger?.finish(`❌ WebSocket 连接错误`, true);
                void stopAudioCapture();
            };

            socket.onclose = () => {
                if (ws === socket) ws = null;
                void stopAudioCapture().finally(resetRecordingControls);
            };
        } catch (error) {
            await stopAudioCapture();
            resetRecordingControls();
            if (realtimeLogger) {
                realtimeLogger.finish(`无法访问麦克风或连接失败: ${error.message}`, true);
            } else {
                addSystemLog(`无法访问麦克风或连接失败: ${error.message}`, true);
            }
        }
    });
"""
content = re.sub(
    r'btnStart\.addEventListener\("click", async \(\) => \{.*?\n    \}\);',
    btn_start_logic.strip(),
    content,
    flags=re.DOTALL
)

with open("frontend/app.js", "w") as f:
    f.write(content)

