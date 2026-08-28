const apiBaseMeta = document.querySelector('meta[name="google-voice-api-origin"]');
const configuredApiOrigin = apiBaseMeta?.content.trim().replace(/\/$/, "");
const isLocalFrontend = ["localhost", "127.0.0.1"].includes(window.location.hostname)
    && window.location.port !== "8000";
const defaultApiOrigin = isLocalFrontend
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : window.location.origin;
const API_BASE = `${configuredApiOrigin || defaultApiOrigin}/api`;
const wsUrl = new URL(API_BASE);
wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
const WS_BASE = wsUrl.toString().replace(/\/$/, "");


class PcmStreamRecorder {
    constructor(stream, onChunk) {
        this.stream = stream;
        this.onChunk = onChunk;
        this.audioContext = null;
        this.sourceNode = null;
        this.processorNode = null;
        this.silentGain = null;
        this.flushResolver = null;
        this.flushTimer = null;
        this.started = false;
        this.usesAudioWorklet = false;
    }

    async initialize() {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) throw new Error("当前浏览器不支持 Web Audio 实时录音");

        this.audioContext = new AudioContextClass();
        await this.audioContext.resume();
        this.sourceNode = this.audioContext.createMediaStreamSource(this.stream);

        if (this.audioContext.audioWorklet && window.AudioWorkletNode) {
            await this.audioContext.audioWorklet.addModule("pcm-recorder-worklet.js");
            this.processorNode = new AudioWorkletNode(
                this.audioContext,
                "pcm-capture-processor",
            );
            this.processorNode.port.onmessage = (event) => this.handleProcessorMessage(event.data);
            this.usesAudioWorklet = true;
        } else if (this.audioContext.createScriptProcessor) {
            // 仅供缺少 AudioWorklet 的嵌入式或旧版浏览器使用。
            this.processorNode = this.audioContext.createScriptProcessor(4096, 1, 1);
            this.processorNode.onaudioprocess = (event) => {
                this.sendFloat32AsPcm(event.inputBuffer.getChannelData(0));
            };
        } else {
            throw new Error("当前浏览器不支持 PCM 实时录音");
        }

        this.silentGain = this.audioContext.createGain();
        this.silentGain.gain.value = 0;
        return this.audioContext.sampleRate;
    }

    start() {
        if (!this.audioContext || !this.sourceNode || !this.processorNode || !this.silentGain) {
            throw new Error("PCM 录音器尚未初始化");
        }
        this.sourceNode.connect(this.processorNode);
        this.processorNode.connect(this.silentGain);
        this.silentGain.connect(this.audioContext.destination);
        this.started = true;
    }

    handleProcessorMessage(message) {
        if (message.type === "chunk" && message.buffer instanceof ArrayBuffer) {
            this.onChunk(message.buffer);
            return;
        }
        if (message.type === "flushed" && this.flushResolver) {
            this.flushResolver();
        }
    }

    sendFloat32AsPcm(samples) {
        const pcmBuffer = new ArrayBuffer(samples.length * 2);
        const dataView = new DataView(pcmBuffer);
        for (let index = 0; index < samples.length; index += 1) {
            const normalizedSample = Math.max(-1, Math.min(1, samples[index]));
            const int16Sample = normalizedSample < 0
                ? normalizedSample * 0x8000
                : normalizedSample * 0x7fff;
            dataView.setInt16(index * 2, int16Sample, true);
        }
        this.onChunk(pcmBuffer);
    }

    async stop() {
        if (!this.audioContext) return;

        if (this.started && this.usesAudioWorklet && this.processorNode) {
            await new Promise((resolve) => {
                this.flushResolver = () => {
                    window.clearTimeout(this.flushTimer);
                    this.flushResolver = null;
                    resolve();
                };
                // 防止页面或浏览器异常导致清理流程永久等待。
                this.flushTimer = window.setTimeout(this.flushResolver, 1000);
                this.processorNode.port.postMessage({ action: "flush" });
            });
        }

        if (this.processorNode && !this.usesAudioWorklet) {
            this.processorNode.onaudioprocess = null;
        }
        this.sourceNode?.disconnect();
        this.processorNode?.disconnect();
        this.silentGain?.disconnect();
        await this.audioContext.close();
        this.audioContext = null;
        this.started = false;
    }
}


document.addEventListener("DOMContentLoaded", () => {
    const btnTts = document.getElementById("btn-tts");
    const ttsAudio = document.getElementById("tts-audio");
    let currentTtsAudioUrl = null;

    btnTts.addEventListener("click", async () => {
        const text = document.getElementById("tts-text").value.trim();
        if (!text) return window.alert("请输入需要转为语音的文字");

        btnTts.disabled = true;
        btnTts.innerText = "音频生成中...";

        try {
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
        } catch (error) {
            window.alert(`TTS 请求失败: ${error.message}`);
        } finally {
            btnTts.disabled = false;
            btnTts.innerText = "生成语音并播放";
        }
    });

    const btnUpload = document.getElementById("btn-upload");
    const resultUpload = document.getElementById("upload-result");

    btnUpload.addEventListener("click", async () => {
        const fileInput = document.getElementById("stt-file-input");
        if (fileInput.files.length === 0) return window.alert("请先选择要识别的音频文件");

        btnUpload.disabled = true;
        btnUpload.innerText = "云端识别中...";
        resultUpload.innerText = "正在上传并等待 Gemini 处理...";
        resultUpload.style.color = "#ff9800";

        const formData = new FormData();
        formData.append("file", fileInput.files[0]);

        try {
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
        } catch (error) {
            resultUpload.innerText = `失败: ${error.message}`;
            resultUpload.style.color = "red";
        } finally {
            btnUpload.disabled = false;
            btnUpload.innerText = "上传并识别";
        }
    });

    let ws = null;
    let localStream = null;
    let pcmRecorder = null;
    let cleanupPromise = null;

    const btnStart = document.getElementById("btn-start-record");
    const btnStop = document.getElementById("btn-stop-record");
    const resultRealtime = document.getElementById("realtime-result");

    function resetRecordingControls() {
        btnStart.disabled = false;
        btnStop.disabled = true;
    }

    async function stopAudioCapture() {
        if (cleanupPromise) return cleanupPromise;
        cleanupPromise = (async () => {
            try {
                // 先刷新 AudioWorklet 尾帧，保证 stop 控制消息排在最后一段音频之后。
                await pcmRecorder?.stop();
            } finally {
                pcmRecorder = null;
                if (localStream) {
                    localStream.getTracks().forEach((track) => track.stop());
                    localStream = null;
                }
            }
        })();
        try {
            await cleanupPromise;
        } finally {
            cleanupPromise = null;
        }
    }

    btnStart.addEventListener("click", async () => {
        btnStart.disabled = true;
        resultRealtime.innerText = "正在申请麦克风并连接服务...";
        resultRealtime.style.color = "#4285f4";

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
                } catch (error) {
                    resultRealtime.innerText = `录音初始化失败: ${error.message}`;
                    resultRealtime.style.color = "red";
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
                        return;
                    }
                    if (data.text) {
                        resultRealtime.innerText = data.text;
                        resultRealtime.style.color = data.is_final ? "#34a853" : "#ff9800";
                    } else if (data.status) {
                        resultRealtime.innerText = data.status;
                        resultRealtime.style.color = "#4285f4";
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
                void stopAudioCapture();
            };

            socket.onclose = () => {
                if (ws === socket) ws = null;
                void stopAudioCapture().finally(resetRecordingControls);
            };
        } catch (error) {
            await stopAudioCapture();
            resetRecordingControls();
            window.alert(`无法访问麦克风或建立连接失败: ${error.message}`);
        }
    });

    btnStop.addEventListener("click", async () => {
        btnStop.disabled = true;
        resultRealtime.innerText = "录音已停止，正在等待最终识别结果...";
        resultRealtime.style.color = "#ff9800";

        await stopAudioCapture();
        if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "stop" }));
        } else {
            resetRecordingControls();
        }
    });

    window.addEventListener("beforeunload", () => {
        if (currentTtsAudioUrl) URL.revokeObjectURL(currentTtsAudioUrl);
        localStream?.getTracks().forEach((track) => track.stop());
        ws?.close();
    });
});
