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
const apiTokenMeta = document.querySelector('meta[name="google-voice-api-token"]');
const API_TOKEN_STORAGE_KEY = "google-voice-api-token";
const STT_TARGET_SAMPLE_RATE = 16000;
const MIN_EDGE_SPEECH_RECOGNITION_VERSION = 87;

function getStoredApiToken() {
    return sessionStorage.getItem(API_TOKEN_STORAGE_KEY)?.trim() || "";
}

function getMetaApiToken() {
    return apiTokenMeta?.content.trim() || "";
}

function maskToken(token) {
    if (token.length <= 8) {
        return "*".repeat(Math.max(token.length, 4));
    }
    return `${token.slice(0, 4)}…${token.slice(-4)}`;
}

function resolveApiToken({ allowPrompt = true } = {}) {
    const fromMeta = getMetaApiToken();
    if (fromMeta) {
        return fromMeta;
    }
    const fromSession = getStoredApiToken();
    if (fromSession) {
        return fromSession;
    }
    if (!allowPrompt) {
        return "";
    }
    // 优先引导用户使用页面认证区，避免依赖可能被浏览器拦截的 window.prompt。
    return "";
}

function requireApiToken() {
    const token = resolveApiToken({ allowPrompt: false });
    if (!token) {
        throw new Error("缺少 API Token，请先在上方输入 Token 并点击「确认认证」");
    }
    return token;
}

function buildApiHeaders(extraHeaders = {}) {
    return {
        ...extraHeaders,
        Authorization: `Bearer ${requireApiToken()}`,
    };
}

function buildSttStreamUrl() {
    const url = new URL(`${WS_BASE}/stt/stream`);
    url.searchParams.set("token", requireApiToken());
    return url.toString();
}


function detectEdgeSpeechRecognition() {
    const edgeBrand = navigator.userAgentData?.brands?.find(
        ({ brand }) => brand === "Microsoft Edge",
    );
    // Edg/ 是桌面 Chromium Edge；EdgA/ 与 EdgiOS/ 不在微软支持范围内。
    const desktopEdgeMatch = navigator.userAgent.match(/\bEdg\/(\d+)/);
    const isMobileEdge = Boolean(
        navigator.userAgentData?.mobile || /\b(?:EdgA|EdgiOS)\//.test(navigator.userAgent),
    );
    const edgeMajorVersion = Number(edgeBrand?.version || desktopEdgeMatch?.[1] || 0);
    const RecognitionConstructor = window.SpeechRecognition || window.webkitSpeechRecognition;

    if ((!edgeBrand && !desktopEdgeMatch) || isMobileEdge) {
        return { available: false, reason: "当前不是受支持的桌面版 Microsoft Edge" };
    }
    if (edgeMajorVersion < MIN_EDGE_SPEECH_RECOGNITION_VERSION) {
        return {
            available: false,
            reason: `Edge ${edgeMajorVersion || "未知版本"} 低于最低版本 87`,
        };
    }
    if (!window.isSecureContext) {
        return { available: false, reason: "当前页面不是安全上下文，无法访问麦克风" };
    }
    if (!RecognitionConstructor) {
        return {
            available: false,
            reason: "SpeechRecognition 不可用，可能被浏览器或企业策略禁用",
        };
    }

    return {
        available: true,
        edgeMajorVersion,
        RecognitionConstructor,
        reason: `Edge ${edgeMajorVersion} SpeechRecognition 可用`,
    };
}


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

        try {
            // 专用实时转录模型以 16kHz 单声道 PCM 获得最稳定的识别效果。
            this.audioContext = new AudioContextClass({ sampleRate: STT_TARGET_SAMPLE_RATE });
        } catch (error) {
            // 少数旧浏览器不接受 sampleRate 选项，退回设备实际采样率并如实上报。
            this.audioContext = new AudioContextClass();
        }
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


function bootApp() {
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

    const authStatus = document.getElementById("auth-status");
    const authTokenInput = document.getElementById("auth-token-input");
    const btnAuthSet = document.getElementById("btn-auth-set");
    const btnAuthClear = document.getElementById("btn-auth-clear");

    function refreshAuthStatus() {
        if (!authStatus || !btnAuthClear) {
            return;
        }
        const metaToken = getMetaApiToken();
        const storedToken = getStoredApiToken();
        authStatus.classList.remove("auth-ready", "auth-missing");

        if (metaToken) {
            authStatus.textContent = `已认证 · ${maskToken(metaToken)}`;
            authStatus.classList.add("auth-ready");
            btnAuthClear.disabled = true;
            if (authTokenInput) {
                authTokenInput.disabled = true;
                authTokenInput.placeholder = "页面 meta 已配置 Token";
            }
            return;
        }
        if (storedToken) {
            authStatus.textContent = `已认证 · ${maskToken(storedToken)}`;
            authStatus.classList.add("auth-ready");
            btnAuthClear.disabled = false;
            if (authTokenInput) {
                authTokenInput.disabled = false;
                authTokenInput.value = "";
                authTokenInput.placeholder = "已认证，更换请重新输入";
            }
            return;
        }
        authStatus.textContent = "未认证";
        authStatus.classList.add("auth-missing");
        btnAuthClear.disabled = true;
        if (authTokenInput) {
            authTokenInput.disabled = false;
            authTokenInput.placeholder = "粘贴 API_ACCESS_TOKEN";
        }
    }

    function saveTokenFromInput() {
        if (getMetaApiToken()) {
            addSystemLog("页面 meta 已配置 Token，优先使用该配置，无需写入 sessionStorage");
            refreshAuthStatus();
            return;
        }
        const entered = authTokenInput?.value.trim() || "";
        if (!entered) {
            addSystemLog("请先在输入框中填写 Token", true);
            authTokenInput?.focus();
            refreshAuthStatus();
            return;
        }
        sessionStorage.setItem(API_TOKEN_STORAGE_KEY, entered);
        authTokenInput.value = "";
        addSystemLog("API Token 已写入 sessionStorage");
        refreshAuthStatus();
    }

    btnAuthSet?.addEventListener("click", saveTokenFromInput);
    authTokenInput?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            saveTokenFromInput();
        }
    });

    btnAuthClear?.addEventListener("click", () => {
        sessionStorage.removeItem(API_TOKEN_STORAGE_KEY);
        if (authTokenInput) {
            authTokenInput.value = "";
        }
        addSystemLog("已清除 sessionStorage 中的 API Token");
        refreshAuthStatus();
    });

    refreshAuthStatus();

    const btnTts = document.getElementById("btn-tts");
    const btnTtsStream = document.getElementById("btn-tts-stream");
    const btnTtsStreamStop = document.getElementById("btn-tts-stream-stop");
    const ttsAudio = document.getElementById("tts-audio");
    let currentTtsAudioUrl = null;
    let currentTtsStreamUrl = null;
    let currentTtsStreamController = null;
    let currentTtsMediaSource = null;

    function resetTtsPlayer() {
        currentTtsStreamController?.abort();
        currentTtsStreamController = null;
        ttsAudio.pause();
        ttsAudio.removeAttribute("src");
        ttsAudio.load();

        if (currentTtsAudioUrl) URL.revokeObjectURL(currentTtsAudioUrl);
        if (currentTtsStreamUrl) URL.revokeObjectURL(currentTtsStreamUrl);
        currentTtsAudioUrl = null;
        currentTtsStreamUrl = null;
        currentTtsMediaSource = null;
        btnTtsStreamStop.disabled = true;
    }

    function waitForMediaSourceOpen(mediaSource, signal) {
        return new Promise((resolve, reject) => {
            if (signal.aborted) {
                reject(new DOMException("流式播放已停止", "AbortError"));
                return;
            }
            const cleanup = () => {
                mediaSource.removeEventListener("sourceopen", handleOpen);
                signal.removeEventListener("abort", handleAbort);
            };
            const handleOpen = () => {
                cleanup();
                resolve();
            };
            const handleAbort = () => {
                cleanup();
                reject(new DOMException("流式播放已停止", "AbortError"));
            };

            mediaSource.addEventListener("sourceopen", handleOpen, { once: true });
            signal.addEventListener("abort", handleAbort, { once: true });
        });
    }

    function appendAudioChunk(sourceBuffer, chunk, signal) {
        return new Promise((resolve, reject) => {
            if (signal.aborted) {
                reject(new DOMException("流式播放已停止", "AbortError"));
                return;
            }
            const cleanup = () => {
                sourceBuffer.removeEventListener("updateend", handleUpdateEnd);
                sourceBuffer.removeEventListener("error", handleError);
                signal.removeEventListener("abort", handleAbort);
            };
            const handleUpdateEnd = () => {
                cleanup();
                resolve();
            };
            const handleError = () => {
                cleanup();
                reject(new Error("浏览器无法解码 Edge TTS 音频分片"));
            };
            const handleAbort = () => {
                cleanup();
                reject(new DOMException("流式播放已停止", "AbortError"));
            };

            sourceBuffer.addEventListener("updateend", handleUpdateEnd, { once: true });
            sourceBuffer.addEventListener("error", handleError, { once: true });
            signal.addEventListener("abort", handleAbort, { once: true });
            try {
                sourceBuffer.appendBuffer(chunk);
            } catch (error) {
                cleanup();
                reject(error);
            }
        });
    }

    btnTts.addEventListener("click", async () => {
        const text = document.getElementById("tts-text").value.trim();
        if (!text) {
            addSystemLog("请输入需要转为语音的文字", true);
            return;
        }

        const logger = new ActionLogger("文字转语音 (TTS)");
        resetTtsPlayer();
        btnTts.disabled = true;
        btnTtsStream.disabled = true;
        btnTts.innerText = "音频生成中...";

        try {
            logger.updateStatus("正在调用后端接口...");
            const response = await fetch(`${API_BASE}/tts`, {
                method: "POST",
                headers: buildApiHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ text }),
            });
            if (!response.ok) {
                const errorBody = await response.json().catch(() => ({}));
                throw new Error(errorBody.detail || `HTTP ${response.status}`);
            }

            const audioBlob = await response.blob();
            currentTtsAudioUrl = URL.createObjectURL(audioBlob);
            ttsAudio.src = currentTtsAudioUrl;
            ttsAudio.style.display = "block";
            await ttsAudio.play();
            logger.finish("✅ TTS 音频生成成功并已播放。");
        } catch (error) {
            logger.finish(`❌ TTS 请求失败: ${error.message}`, true);
        } finally {
            btnTts.disabled = false;
            btnTtsStream.disabled = false;
            btnTts.innerText = "生成语音并播放";
        }
    });

    btnTtsStream.addEventListener("click", async () => {
        const text = document.getElementById("tts-text").value.trim();
        if (!text) {
            addSystemLog("请输入需要转为语音的文字", true);
            return;
        }
        if (!window.MediaSource || !MediaSource.isTypeSupported("audio/mpeg")) {
            addSystemLog("当前浏览器不支持 MP3 流式播放，请使用 Chrome 或 Edge", true);
            return;
        }

        const logger = new ActionLogger("Edge TTS 流式播放");
        resetTtsPlayer();
        const streamController = new AbortController();
        currentTtsStreamController = streamController;
        btnTts.disabled = true;
        btnTtsStream.disabled = true;
        btnTtsStreamStop.disabled = false;
        btnTtsStream.innerText = "正在连接 Edge...";

        try {
            logger.updateStatus("正在等待首个音频分片...");
            const response = await fetch(`${API_BASE}/tts/stream`, {
                method: "POST",
                headers: buildApiHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ text }),
                signal: streamController.signal,
            });
            if (!response.ok) {
                const errorBody = await response.json().catch(() => ({}));
                throw new Error(errorBody.detail || `HTTP ${response.status}`);
            }
            if (!response.body) throw new Error("浏览器未提供流式响应读取能力");

            currentTtsMediaSource = new MediaSource();
            currentTtsStreamUrl = URL.createObjectURL(currentTtsMediaSource);
            ttsAudio.src = currentTtsStreamUrl;
            ttsAudio.style.display = "block";
            await waitForMediaSourceOpen(currentTtsMediaSource, streamController.signal);

            const sourceBuffer = currentTtsMediaSource.addSourceBuffer("audio/mpeg");
            const reader = response.body.getReader();
            let playbackStarted = false;

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                if (!value?.byteLength) continue;

                await appendAudioChunk(sourceBuffer, value, streamController.signal);
                if (!playbackStarted) {
                    playbackStarted = true;
                    await ttsAudio.play();
                    logger.updateStatus("已开始播放，正在接收后续音频分片...");
                }
            }

            if (!playbackStarted) throw new Error("Edge TTS 未返回可播放音频");
            if (currentTtsMediaSource.readyState === "open") {
                currentTtsMediaSource.endOfStream();
            }
            logger.finish("✅ Edge TTS 音频已流式接收并播放。");
        } catch (error) {
            if (error.name === "AbortError") {
                logger.finish("⏹ Edge TTS 流式播放已停止。");
            } else {
                resetTtsPlayer();
                logger.finish(`❌ Edge TTS 流式播放失败: ${error.message}`, true);
            }
        } finally {
            if (currentTtsStreamController === streamController) {
                currentTtsStreamController = null;
            }
            btnTts.disabled = false;
            btnTtsStream.disabled = false;
            btnTtsStream.innerText = "Edge 流式播放";
        }
    });

    btnTtsStreamStop.addEventListener("click", () => {
        resetTtsPlayer();
        addSystemLog("Edge TTS 流式播放已停止");
    });

    ttsAudio.addEventListener("ended", () => {
        btnTtsStreamStop.disabled = true;
    });

    const btnUpload = document.getElementById("btn-upload");
    const resultUpload = document.getElementById("upload-result");

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
                headers: buildApiHeaders(),
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

    let ws = null;
    let localStream = null;
    let pcmRecorder = null;
    let cleanupPromise = null;
    let realtimeLogger = null;
    let activeRecognitionMode = null;
    let browserRecognition = null;
    let browserRecognitionShouldRun = false;
    let browserRecognitionFallbackStarted = false;
    let browserRecognitionFinalText = "";
    let browserRecognitionTerminalError = false;
    let browserRecognitionRestartTimer = null;

    const btnStart = document.getElementById("btn-start-record");
    const btnStop = document.getElementById("btn-stop-record");
    const resultRealtime = document.getElementById("realtime-result");
    const realtimeEngine = document.getElementById("stt-realtime-engine");
    const edgeSpeechSupport = detectEdgeSpeechRecognition();

    realtimeEngine.innerText = edgeSpeechSupport.available
        ? `优先引擎：Edge ${edgeSpeechSupport.edgeMajorVersion} SpeechRecognition；不可用时自动切换后端。`
        : `识别引擎：后端 WebSocket（${edgeSpeechSupport.reason}）。`;

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

    function clearBrowserRecognitionRestart() {
        if (browserRecognitionRestartTimer) {
            window.clearTimeout(browserRecognitionRestartTimer);
            browserRecognitionRestartTimer = null;
        }
    }

    function browserSpeechErrorMessage(errorCode) {
        const messages = {
            "audio-capture": "浏览器无法获取麦克风音频",
            "language-not-supported": "Edge SpeechRecognition 不支持简体中文",
            "language-unavailable": "Edge 简体中文识别服务当前不可用",
            network: "Edge SpeechRecognition 网络服务不可用",
            "no-speech": "暂未检测到语音，请继续说话",
            "not-allowed": "麦克风权限被拒绝",
            "service-not-allowed": "Edge SpeechRecognition 被浏览器策略禁用",
        };
        return messages[errorCode] || `Edge SpeechRecognition 错误：${errorCode}`;
    }

    function shouldFallbackFromBrowserSpeech(errorCode) {
        return new Set([
            "language-not-supported",
            "language-unavailable",
            "network",
            "service-not-allowed",
        ]).has(errorCode);
    }

    async function startWebSocketRecognition(fallbackReason = "") {
        activeRecognitionMode = "websocket";
        resultRealtime.innerText = fallbackReason
            ? "浏览器识别不可用，正在连接后端识别服务..."
            : "正在申请麦克风并连接后端识别服务...";
        resultRealtime.style.color = "#4285f4";
        realtimeLogger.updateStatus(
            fallbackReason
                ? `${fallbackReason}，正在切换后端 WebSocket...`
                : "正在使用后端 WebSocket 识别...",
        );

        try {
            localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const socket = new WebSocket(buildSttStreamUrl());
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
                    resultRealtime.innerText = "后端识别已连接，请开始说话...";
                    btnStop.disabled = false;
                    realtimeLogger.updateStatus("✅ 后端识别已连接，等待识别结果...");
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
                socket.close();
            };

            socket.onclose = () => {
                const wasActiveSocket = ws === socket;
                if (wasActiveSocket) ws = null;
                void stopAudioCapture().finally(() => {
                    if (wasActiveSocket) {
                        activeRecognitionMode = null;
                        resetRecordingControls();
                    }
                });
            };
        } catch (error) {
            await stopAudioCapture();
            activeRecognitionMode = null;
            resetRecordingControls();
            if (realtimeLogger) {
                realtimeLogger.finish(`无法访问麦克风或连接失败: ${error.message}`, true);
            } else {
                addSystemLog(`无法访问麦克风或连接失败: ${error.message}`, true);
            }
        }
    }

    async function fallbackBrowserSpeechToWebSocket(recognition, reason) {
        if (browserRecognitionFallbackStarted || browserRecognition !== recognition) return;

        browserRecognitionFallbackStarted = true;
        browserRecognitionShouldRun = false;
        clearBrowserRecognitionRestart();
        realtimeLogger.updateStatus(`${reason}，准备切换后端识别...`);

        try {
            recognition.abort();
        } catch (error) {
            // 识别实例可能已经自行结束；继续执行后端降级即可。
        }

        // 给浏览器一个事件循环释放原生麦克风，再由后端路径重新申请。
        await new Promise((resolve) => window.setTimeout(resolve, 100));
        if (browserRecognition === recognition) browserRecognition = null;
        await startWebSocketRecognition(reason);
    }

    function startBrowserSpeechRecognition() {
        const recognition = new edgeSpeechSupport.RecognitionConstructor();
        browserRecognition = recognition;
        browserRecognitionShouldRun = true;
        browserRecognitionFallbackStarted = false;
        browserRecognitionFinalText = "";
        browserRecognitionTerminalError = false;
        activeRecognitionMode = "browser";

        recognition.lang = "zh-CN";
        if ("processLocally" in recognition) {
            // Edge 150 的本地模型尚不支持中文，使用浏览器自带的远程识别服务。
            recognition.processLocally = false;
        }
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.maxAlternatives = 1;

        recognition.onstart = () => {
            if (browserRecognition !== recognition) return;
            resultRealtime.innerText = "Edge 浏览器识别已启动，请开始说话...";
            resultRealtime.style.color = "#4285f4";
            btnStop.disabled = false;
            realtimeLogger.updateStatus(
                `✅ Edge ${edgeSpeechSupport.edgeMajorVersion} SpeechRecognition 已启动，未调用后端接口。`,
            );
        };

        recognition.onresult = (event) => {
            if (browserRecognition !== recognition) return;
            let interimText = "";

            for (let index = event.resultIndex; index < event.results.length; index += 1) {
                const transcript = event.results[index][0]?.transcript?.trim();
                if (!transcript) continue;
                if (event.results[index].isFinal) {
                    browserRecognitionFinalText = [browserRecognitionFinalText, transcript]
                        .filter(Boolean)
                        .join(" ");
                } else {
                    interimText = [interimText, transcript].filter(Boolean).join(" ");
                }
            }

            const displayText = [browserRecognitionFinalText, interimText]
                .filter(Boolean)
                .join(" ");
            if (displayText) {
                resultRealtime.innerText = displayText;
                resultRealtime.style.color = interimText ? "#ff9800" : "#34a853";
                realtimeLogger.updateStatus(
                    interimText ? "Edge 返回部分识别结果..." : "Edge 返回最终识别片段...",
                );
            }
        };

        recognition.onerror = (event) => {
            if (browserRecognition !== recognition) return;
            const errorCode = event.error || "unknown";
            if (errorCode === "aborted" && !browserRecognitionShouldRun) return;
            if (errorCode === "no-speech") {
                realtimeLogger.updateStatus(browserSpeechErrorMessage(errorCode));
                return;
            }

            const message = browserSpeechErrorMessage(errorCode);
            if (browserRecognitionShouldRun && shouldFallbackFromBrowserSpeech(errorCode)) {
                void fallbackBrowserSpeechToWebSocket(recognition, message);
                return;
            }

            browserRecognitionShouldRun = false;
            browserRecognitionTerminalError = true;
            resultRealtime.innerText = message;
            resultRealtime.style.color = "red";
            realtimeLogger.finish(`❌ ${message}`, true);
        };

        recognition.onend = () => {
            if (browserRecognition !== recognition || browserRecognitionFallbackStarted) return;
            if (browserRecognitionShouldRun) {
                // Edge 可能在长时间静音后自行结束，用户未停止时自动继续监听。
                browserRecognitionRestartTimer = window.setTimeout(() => {
                    if (browserRecognition !== recognition || !browserRecognitionShouldRun) return;
                    try {
                        recognition.start();
                    } catch (error) {
                        void fallbackBrowserSpeechToWebSocket(
                            recognition,
                            `Edge SpeechRecognition 无法继续：${error.message}`,
                        );
                    }
                }, 200);
                return;
            }

            clearBrowserRecognitionRestart();
            browserRecognition = null;
            activeRecognitionMode = null;
            resetRecordingControls();
            if (browserRecognitionFinalText && !browserRecognitionTerminalError) {
                resultRealtime.innerText = browserRecognitionFinalText;
                resultRealtime.style.color = "#34a853";
                realtimeLogger.finish(`✅ 最终识别结果: ${browserRecognitionFinalText}`);
            } else if (!browserRecognitionTerminalError) {
                resultRealtime.innerText = "录音已停止，未识别到有效语音";
                resultRealtime.style.color = "#5f6368";
                realtimeLogger.finish("录音已停止，未识别到有效语音。");
            }
        };

        try {
            recognition.start();
        } catch (error) {
            browserRecognitionShouldRun = false;
            browserRecognition = null;
            activeRecognitionMode = null;
            throw error;
        }
    }

    btnStart.addEventListener("click", async () => {
        btnStart.disabled = true;
        resultRealtime.style.color = "#4285f4";
        realtimeLogger = new ActionLogger("实时语音转文字");

        if (edgeSpeechSupport.available) {
            resultRealtime.innerText = `检测到 Edge ${edgeSpeechSupport.edgeMajorVersion}，正在启动浏览器识别...`;
            realtimeLogger.updateStatus("优先启动 Edge SpeechRecognition，不调用后端接口...");
            try {
                startBrowserSpeechRecognition();
                return;
            } catch (error) {
                await startWebSocketRecognition(
                    `Edge SpeechRecognition 启动失败：${error.message}`,
                );
                return;
            }
        }

        await startWebSocketRecognition(edgeSpeechSupport.reason);
    });

    btnStop.addEventListener("click", async () => {
        btnStop.disabled = true;
        resultRealtime.innerText = "录音已停止，正在等待最终识别结果...";
        resultRealtime.style.color = "#ff9800";

        if (activeRecognitionMode === "browser" && browserRecognition) {
            browserRecognitionShouldRun = false;
            clearBrowserRecognitionRestart();
            try {
                // stop 会尽量返回最后一段结果；abort 会直接丢弃，故优先使用 stop。
                browserRecognition.stop();
            } catch (error) {
                try {
                    browserRecognition.abort();
                } catch (abortError) {
                    // 实例已经结束时无需再次终止，直接恢复界面状态。
                }
                browserRecognition = null;
                activeRecognitionMode = null;
                resetRecordingControls();
            }
            return;
        }

        await stopAudioCapture();
        if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "stop" }));
        } else {
            resetRecordingControls();
        }
    });

    window.addEventListener("beforeunload", () => {
        if (currentTtsAudioUrl) URL.revokeObjectURL(currentTtsAudioUrl);
        browserRecognitionShouldRun = false;
        clearBrowserRecognitionRestart();
        browserRecognition?.abort();
        localStream?.getTracks().forEach((track) => track.stop());
        ws?.close();
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootApp);
} else {
    bootApp();
}