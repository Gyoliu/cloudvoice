const FRAME_DURATION_SECONDS = 0.1;


class PcmCaptureProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.frameSampleCount = Math.max(1, Math.round(sampleRate * FRAME_DURATION_SECONDS));
        this.frameBuffer = new Int16Array(this.frameSampleCount);
        this.writeIndex = 0;
        this.port.onmessage = (event) => {
            if (event.data?.action === "flush") {
                this.emitFrame();
                this.port.postMessage({ type: "flushed" });
            }
        };
    }

    process(inputs) {
        const inputChannel = inputs[0]?.[0];
        if (!inputChannel) return true;

        for (const sample of inputChannel) {
            const normalizedSample = Math.max(-1, Math.min(1, sample));
            this.frameBuffer[this.writeIndex] = normalizedSample < 0
                ? normalizedSample * 0x8000
                : normalizedSample * 0x7fff;
            this.writeIndex += 1;
            if (this.writeIndex === this.frameSampleCount) this.emitFrame();
        }
        return true;
    }

    emitFrame() {
        if (this.writeIndex === 0) return;
        // 复制有效数据后转移所有权，避免复用已被 detach 的 ArrayBuffer。
        const output = this.frameBuffer.slice(0, this.writeIndex);
        this.port.postMessage(
            { type: "chunk", buffer: output.buffer },
            [output.buffer],
        );
        this.frameBuffer = new Int16Array(this.frameSampleCount);
        this.writeIndex = 0;
    }
}


registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
