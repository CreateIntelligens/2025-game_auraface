/**
 * AuraFace Viewer SDK - 只讀人臉識別功能
 * 適用於第三方整合，只提供識別結果顯示
 * 版本: 1.0.0
 */

class AuraFaceViewer {
    constructor(config = {}) {
        this.config = {
            wsUrl: config.wsUrl || 'ws://localhost:7861',
            container: config.container || document.body,
            autoConnect: config.autoConnect !== false,
            showStats: config.showStats !== false,
            frameRate: config.frameRate || 5, // FPS
            ...config
        };
        
        this.websocket = null;
        this.videoStream = null;
        this.isStreaming = false;
        this.frameInterval = null;
        this.currentFaces = [];
        
        this.elements = {};
        this.callbacks = {
            onConnected: config.onConnected || (() => {}),
            onDisconnected: config.onDisconnected || (() => {}),
            onFaceDetected: config.onFaceDetected || (() => {}),
            onError: config.onError || (() => {})
        };
        
        this.init();
    }
    
    init() {
        this.createUI();
        if (this.config.autoConnect) {
            this.connect();
        }
    }
    
    createUI() {
        const container = typeof this.config.container === 'string' 
            ? document.querySelector(this.config.container) 
            : this.config.container;
        
        container.innerHTML = `
            <div class="auraface-viewer">
                <style>
                    .auraface-viewer {
                        font-family: Arial, sans-serif;
                        max-width: 800px;
                        margin: 0 auto;
                    }
                    .auraface-status {
                        padding: 10px;
                        margin: 10px 0;
                        border-radius: 5px;
                        text-align: center;
                    }
                    .auraface-status.connected {
                        background: #d4edda;
                        color: #155724;
                        border: 1px solid #c3e6cb;
                    }
                    .auraface-status.disconnected {
                        background: #f8d7da;
                        color: #721c24;
                        border: 1px solid #f5c6cb;
                    }
                    .auraface-video-container {
                        position: relative;
                        display: inline-block;
                        margin: 20px 0;
                    }
                    .auraface-video {
                        width: 100%;
                        max-width: 640px;
                        border: 2px solid #ddd;
                        border-radius: 5px;
                        transform: scaleX(-1); /* 鏡像翻轉顯示 */
                    }
                    .auraface-overlay {
                        position: absolute;
                        top: 0;
                        left: 0;
                        pointer-events: none;
                        border: 2px solid #ddd;
                        border-radius: 5px;
                        /* overlay 不翻轉，直接顯示正確座標 */
                    }
                    .auraface-controls {
                        margin: 10px 0;
                    }
                    .auraface-btn {
                        padding: 10px 20px;
                        margin: 5px;
                        border: none;
                        border-radius: 5px;
                        cursor: pointer;
                        font-size: 16px;
                    }
                    .auraface-btn.primary {
                        background: #007bff;
                        color: white;
                    }
                    .auraface-btn.danger {
                        background: #dc3545;
                        color: white;
                    }
                    .auraface-stats {
                        background: #f8f9fa;
                        padding: 15px;
                        border-radius: 5px;
                        margin: 20px 0;
                    }
                    .auraface-info {
                        background: #e9ecef;
                        padding: 10px;
                        border-radius: 5px;
                        font-family: monospace;
                        font-size: 14px;
                        margin: 10px 0;
                    }
                </style>
                
                <div class="auraface-status disconnected" id="auraface-status">
                    未連接到伺服器
                </div>
                
                <div class="auraface-controls">
                    <button class="auraface-btn primary" id="auraface-start-camera">啟動攝像頭</button>
                    <button class="auraface-btn danger" id="auraface-stop-camera" disabled>停止攝像頭</button>
                </div>
                
                <div class="auraface-video-container">
                    <video class="auraface-video" id="auraface-video" autoplay muted></video>
                    <canvas class="auraface-overlay" id="auraface-overlay"></canvas>
                </div>
                
                <div class="auraface-info" id="auraface-info">等待識別結果...</div>
                
                ${this.config.showStats ? `
                <div class="auraface-stats" id="auraface-stats">
                    <div><strong>即時統計</strong></div>
                    <div>等待數據...</div>
                </div>
                ` : ''}
            </div>
        `;
        
        // 綁定元素
        this.elements = {
            status: container.querySelector('#auraface-status'),
            video: container.querySelector('#auraface-video'),
            overlay: container.querySelector('#auraface-overlay'),
            info: container.querySelector('#auraface-info'),
            stats: container.querySelector('#auraface-stats'),
            startBtn: container.querySelector('#auraface-start-camera'),
            stopBtn: container.querySelector('#auraface-stop-camera')
        };
        
        this.overlayCtx = this.elements.overlay.getContext('2d');
        
        // 綁定事件
        this.elements.startBtn.addEventListener('click', () => this.startCamera());
        this.elements.stopBtn.addEventListener('click', () => this.stopCamera());
    }
    
    connect() {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            return;
        }
        
        this.elements.status.textContent = '正在連接伺服器...';
        this.elements.status.className = 'auraface-status disconnected';
        
        try {
            this.websocket = new WebSocket(this.config.wsUrl);
            
            this.websocket.onopen = (event) => {
                this.elements.status.textContent = '已連接到伺服器';
                this.elements.status.className = 'auraface-status connected';
                this.callbacks.onConnected(event);
            };
            
            this.websocket.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            };
            
            this.websocket.onclose = (event) => {
                this.elements.status.textContent = `與伺服器斷線 (${event.code})`;
                this.elements.status.className = 'auraface-status disconnected';
                this.callbacks.onDisconnected(event);
                
                // 自動重連
                if (event.code !== 1000) {
                    setTimeout(() => this.connect(), 5000);
                }
            };
            
            this.websocket.onerror = (error) => {
                this.elements.status.textContent = 'WebSocket 連接錯誤';
                this.elements.status.className = 'auraface-status disconnected';
                this.callbacks.onError(error);
            };
        } catch (error) {
            this.callbacks.onError(error);
        }
    }
    
    disconnect() {
        if (this.websocket) {
            this.websocket.close(1000);
            this.websocket = null;
        }
        this.stopStreaming();
    }
    
    async startCamera() {
        try {
            this.videoStream = await navigator.mediaDevices.getUserMedia({
                video: { width: 640, height: 480 }
            });
            this.elements.video.srcObject = this.videoStream;
            
            this.elements.startBtn.disabled = true;
            this.elements.stopBtn.disabled = false;
            
            this.startStreaming();
        } catch (error) {
            this.callbacks.onError(error);
        }
    }
    
    stopCamera() {
        if (this.videoStream) {
            this.videoStream.getTracks().forEach(track => track.stop());
            this.videoStream = null;
            this.elements.video.srcObject = null;
        }
        
        this.elements.startBtn.disabled = false;
        this.elements.stopBtn.disabled = true;
        
        this.stopStreaming();
    }
    
    startStreaming() {
        if (this.isStreaming || !this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
            return;
        }
        
        this.isStreaming = true;
        this.lastSendTime = 0;
        this.isProcessing = false; // 防止幀積壓
        this.delayStats = {
            recentDelays: [],
            maxDelay: 0,
            avgDelay: 0,
            warningCount: 0
        };
        const interval = 1000 / this.config.frameRate;
        
        this.frameInterval = setInterval(() => {
            const now = Date.now();
            const timeSinceLastSend = now - this.lastSendTime;
            
            // 動態調整發送間隔，根據延遲狀況
            let dynamicInterval = interval;
            if (this.delayStats.avgDelay > 800) {
                dynamicInterval = interval * 2; // 延遲高時減半頻率
            } else if (this.delayStats.avgDelay > 500) {
                dynamicInterval = interval * 1.5; // 延遲中等時降低頻率
            }
            
            // 只有在不忙碌且達到動態間隔時間才發送
            if (!this.isProcessing && 
                timeSinceLastSend >= dynamicInterval &&
                this.elements.video.videoWidth > 0 && 
                this.elements.video.videoHeight > 0) {
                
                // 如果已經超過 2 秒沒回應，強制重置
                if (this.isProcessing && timeSinceLastSend > 2000) {
                    console.warn('⚠️ 強制重置處理狀態 (超過2秒無回應)');
                    this.isProcessing = false;
                }
                
                this.sendFrame();
                this.lastSendTime = now;
            }
        }, Math.min(interval, 100)); // 最少100ms檢查一次
    }
    
    stopStreaming() {
        this.isStreaming = false;
        if (this.frameInterval) {
            clearInterval(this.frameInterval);
            this.frameInterval = null;
        }
    }
    
    sendFrame() {
        if (this.websocket.readyState !== WebSocket.OPEN || this.isProcessing) return;
        
        this.isProcessing = true; // 標記為處理中
        
        const canvas = document.createElement('canvas');
        // 進一步降低解析度提升速度
        canvas.width = Math.min(this.elements.video.videoWidth, 480);
        canvas.height = Math.min(this.elements.video.videoHeight, 360);
        
        const ctx = canvas.getContext('2d');
        // 發送原始影像數據，不翻轉
        ctx.drawImage(this.elements.video, 0, 0, canvas.width, canvas.height);
        
        // 降低品質加快編碼
        const imageData = canvas.toDataURL('image/jpeg', 0.5);
        
        const clientTimestamp = Date.now();
        try {
            this.websocket.send(JSON.stringify({
                type: 'video_frame',
                image: imageData,
                client_timestamp: clientTimestamp // 客戶端時間戳
            }));
        } catch (error) {
            this.isProcessing = false;
            this.callbacks.onError(error);
        }
    }
    
    handleMessage(data) {
        switch (data.type) {
            case 'recognition_result':
                this.isProcessing = false; // 收到回應，解除處理標記
                
                // 計算延遲
                if (data.client_timestamp) {
                    const delay = Date.now() - data.client_timestamp;
                    this.updateDelayStats(delay);
                }
                
                this.displayResult(data);
                this.callbacks.onFaceDetected(data);
                break;
            case 'connection_status':
                break; // 已在 onopen 處理
            case 'error':
                this.isProcessing = false; // 錯誤時也解除標記
                this.callbacks.onError(new Error(data.message));
                break;
        }
    }
    
    updateDelayStats(delay) {
        this.delayStats.recentDelays.push(delay);
        
        // 只保留最近 20 次的延遲記錄
        if (this.delayStats.recentDelays.length > 20) {
            this.delayStats.recentDelays.shift();
        }
        
        // 更新統計
        this.delayStats.maxDelay = Math.max(this.delayStats.maxDelay, delay);
        this.delayStats.avgDelay = this.delayStats.recentDelays.reduce((a, b) => a + b, 0) / this.delayStats.recentDelays.length;
        
        // 延遲警告
        if (delay > 1000) { // 超過 1 秒
            this.delayStats.warningCount++;
            console.warn(`🐌 延遲警告: ${delay}ms (平均: ${this.delayStats.avgDelay.toFixed(0)}ms, 最大: ${this.delayStats.maxDelay}ms)`);
            
            // 連續 3 次警告就重置連接
            if (this.delayStats.warningCount >= 3) {
                console.warn('🔄 延遲過高，嘗試重置連接...');
                this.resetConnection();
            }
        } else if (delay < 500) {
            // 延遲正常時重置警告計數
            this.delayStats.warningCount = Math.max(0, this.delayStats.warningCount - 1);
        }
    }
    
    resetConnection() {
        console.log('🔄 重置 WebSocket 連接...');
        this.stopStreaming();
        this.disconnect();
        
        // 重置統計
        this.delayStats = {
            recentDelays: [],
            maxDelay: 0,
            avgDelay: 0,
            warningCount: 0
        };
        
        // 2 秒後重新連接
        setTimeout(() => {
            this.connect();
            if (this.videoStream) {
                setTimeout(() => this.startStreaming(), 1000);
            }
        }, 2000);
    }
    
    displayResult(data) {
        // 更新資訊
        const delayInfo = this.delayStats.recentDelays.length > 0 ? 
            ` | 延遲: ${this.delayStats.avgDelay.toFixed(0)}ms (最大: ${this.delayStats.maxDelay}ms)` : '';
        
        const info = `
FPS: ${data.fps} | 處理時間: ${data.processing_time}ms${delayInfo}
檢測到 ${data.faces.length} 張人臉
${data.faces.map(face => 
    face.person_id !== 'unknown' ? 
        `✅ ${face.name} (${face.role}) - 信心度: ${face.confidence.toFixed(3)}` : 
        '❓ 未知人員'
).join('\\n')}
時間: ${data.timestamp}
        `;
        this.elements.info.textContent = info;
        
        // 更新統計
        if (this.config.showStats && this.elements.stats) {
            const statsHtml = `
                <div><strong>即時統計</strong></div>
                <div>FPS: ${data.fps}</div>
                <div>處理時間: ${data.processing_time}ms</div>
                <div>檢測人臉: ${data.faces.length}</div>
                <div>已識別: ${data.faces.filter(f => f.person_id !== 'unknown').length}</div>
                <div>未知: ${data.faces.filter(f => f.person_id === 'unknown').length}</div>
            `;
            this.elements.stats.innerHTML = statsHtml;
        }
        
        // 儲存並繪製
        this.currentFaces = data.faces;
        this.drawFaceOverlay();
    }
    
    drawFaceOverlay() {
        if (!this.elements.video.videoWidth || !this.elements.video.videoHeight) return;
        
        // 設置畫布大小為影片顯示大小（而非原始大小）
        const videoRect = this.elements.video.getBoundingClientRect();
        const displayWidth = this.elements.video.offsetWidth;
        const displayHeight = this.elements.video.offsetHeight;
        
        this.elements.overlay.width = displayWidth;
        this.elements.overlay.height = displayHeight;
        this.elements.overlay.style.width = displayWidth + 'px';
        this.elements.overlay.style.height = displayHeight + 'px';
        
        this.overlayCtx.clearRect(0, 0, displayWidth, displayHeight);
        
        // 計算縮放比例
        const videoWidth = this.elements.video.videoWidth;
        const videoHeight = this.elements.video.videoHeight;
        const scaleX = displayWidth / videoWidth;
        const scaleY = displayHeight / videoHeight;
        
        this.currentFaces.forEach(face => {
            const [x1, y1, x2, y2] = face.bbox;
            
            // 後端返回原始影像座標，但用戶看到翻轉的video，需要翻轉座標匹配
            const mirrorX1 = videoWidth - x2;
            const mirrorX2 = videoWidth - x1;
            
            // 將翻轉後的座標轉換為顯示座標
            const displayX1 = mirrorX1 * scaleX;
            const displayY1 = y1 * scaleY;
            const displayX2 = mirrorX2 * scaleX;
            const displayY2 = y2 * scaleY;
            
            let color;
            if (face.person_id === 'unknown') {
                color = '#ff0000'; // 紅色
            } else if (face.role === '員工') {
                color = '#00ff00'; // 綠色
            } else if (face.role === '訪客') {
                color = '#ffff00'; // 黃色
            } else {
                color = '#ff0000'; // 紅色
            }
            
            // 繪製邊框
            this.overlayCtx.strokeStyle = color;
            this.overlayCtx.lineWidth = 2;
            this.overlayCtx.strokeRect(displayX1, displayY1, displayX2 - displayX1, displayY2 - displayY1);
            
            // 繪製標籤（僅已識別）
            if (face.person_id !== 'unknown') {
                const labelWidth = Math.max(120 * scaleX, face.name.length * 8 * scaleX);
                const labelHeight = 50 * scaleY;
                
                this.overlayCtx.fillStyle = color;
                this.overlayCtx.fillRect(displayX1, displayY1 - labelHeight, labelWidth, labelHeight);
                
                this.overlayCtx.fillStyle = '#ffffff';
                this.overlayCtx.font = `${Math.round(12 * Math.min(scaleX, scaleY))}px Arial`;
                this.overlayCtx.fillText(
                    face.role === '員工' ? '[Staff]' : '[Visitor]', 
                    displayX1 + 5, 
                    displayY1 - labelHeight + 15
                );
                this.overlayCtx.font = `${Math.round(14 * Math.min(scaleX, scaleY))}px Arial`;
                this.overlayCtx.fillText(face.name, displayX1 + 5, displayY1 - labelHeight + 30);
                this.overlayCtx.font = `${Math.round(10 * Math.min(scaleX, scaleY))}px Arial`;
                this.overlayCtx.fillText(
                    face.confidence.toFixed(3), 
                    displayX1 + 5, 
                    displayY1 - 5
                );
            }
        });
    }
    
    // 公開 API
    getConnectionStatus() {
        return this.websocket ? this.websocket.readyState : WebSocket.CLOSED;
    }
    
    getCurrentFaces() {
        return [...this.currentFaces];
    }
    
    destroy() {
        this.disconnect();
        this.stopCamera();
        if (this.elements.status && this.elements.status.parentNode) {
            this.elements.status.parentNode.innerHTML = '';
        }
    }
}

// 支援 CommonJS, AMD, 和全域變數
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AuraFaceViewer;
} else if (typeof define === 'function' && define.amd) {
    define([], function() { return AuraFaceViewer; });
} else {
    window.AuraFaceViewer = AuraFaceViewer;
}