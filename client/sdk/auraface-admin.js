/**
 * AuraFace Admin SDK - 完整管理功能
 * 適用於管理員，包含註冊、統計、管理等所有功能
 * 版本: 1.0.0
 */

class AuraFaceAdmin {
    constructor(config = {}) {
        this.config = {
            wsUrl: config.wsUrl || 'ws://localhost:7861',
            container: config.container || document.body,
            autoConnect: config.autoConnect !== false,
            enableRegistration: config.enableRegistration !== false,
            enableStats: config.enableStats !== false,
            frameRate: config.frameRate || 5,
            ...config
        };
        
        this.websocket = null;
        this.videoStream = null;
        this.isStreaming = false;
        this.frameInterval = null;
        this.currentFaces = [];
        this.globalStats = {
            total_frames: 0,
            faces_detected: 0,
            employees_detected: 0,
            visitors_detected: 0,
            unknown_detected: 0
        };
        
        this.elements = {};
        this.callbacks = {
            onConnected: config.onConnected || (() => {}),
            onDisconnected: config.onDisconnected || (() => {}),
            onFaceDetected: config.onFaceDetected || (() => {}),
            onRegistrationComplete: config.onRegistrationComplete || (() => {}),
            onError: config.onError || (() => {})
        };
        
        this.init();
    }
    
    init() {
        this.createUI();
        if (this.config.autoConnect) {
            this.connect();
        }
        
        // 定期請求統計資料
        setInterval(() => this.requestStats(), 5000);
    }
    
    createUI() {
        const container = typeof this.config.container === 'string' 
            ? document.querySelector(this.config.container) 
            : this.config.container;
        
        container.innerHTML = `
            <div class="auraface-admin">
                <style>
                    .auraface-admin {
                        font-family: Arial, sans-serif;
                        max-width: 1200px;
                        margin: 0 auto;
                        padding: 20px;
                    }
                    .auraface-container {
                        background: white;
                        padding: 20px;
                        border-radius: 10px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                        margin-bottom: 20px;
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
                        display: flex;
                        gap: 20px;
                        margin: 20px 0;
                    }
                    .auraface-video-section {
                        flex: 1;
                    }
                    .auraface-video-wrapper {
                        position: relative;
                        display: inline-block;
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
                    .auraface-btn.primary { background: #007bff; color: white; }
                    .auraface-btn.danger { background: #dc3545; color: white; }
                    .auraface-btn.success { background: #28a745; color: white; }
                    .auraface-btn:disabled { opacity: 0.6; cursor: not-allowed; }
                    .auraface-stats-grid {
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 15px;
                        margin: 20px 0;
                    }
                    .auraface-stat-card {
                        background: #f8f9fa;
                        padding: 15px;
                        border-radius: 5px;
                        text-align: center;
                    }
                    .auraface-stat-number {
                        font-size: 2em;
                        font-weight: bold;
                        color: #007bff;
                    }
                    .auraface-info {
                        background: #e9ecef;
                        padding: 10px;
                        border-radius: 5px;
                        font-family: monospace;
                        font-size: 14px;
                        margin: 10px 0;
                    }
                    .auraface-register-form {
                        background: #fff3cd;
                        padding: 15px;
                        border-radius: 5px;
                        margin: 20px 0;
                    }
                    .auraface-form-group {
                        margin-bottom: 15px;
                    }
                    .auraface-label {
                        display: block;
                        margin-bottom: 5px;
                        font-weight: bold;
                    }
                    .auraface-input, .auraface-select {
                        width: 100%;
                        padding: 8px;
                        border: 1px solid #ddd;
                        border-radius: 3px;
                        font-size: 14px;
                        box-sizing: border-box;
                    }
                    .auraface-realtime-stats {
                        background: #f8f9fa;
                        padding: 15px;
                        border-radius: 5px;
                        min-height: 200px;
                    }
                </style>
                
                <div class="auraface-container">
                    <h1>🔍 AuraFace 管理系統</h1>
                    
                    <div class="auraface-status disconnected" id="auraface-status">
                        未連接到伺服器
                    </div>
                    
                    <div class="auraface-controls">
                        <button class="auraface-btn primary" id="auraface-start-camera">啟動攝像頭</button>
                        <button class="auraface-btn danger" id="auraface-stop-camera" disabled>停止攝像頭</button>
                        <button class="auraface-btn success" id="auraface-connect">連接WebSocket</button>
                    </div>
                    
                    <div class="auraface-video-container">
                        <div class="auraface-video-section">
                            <h3>攝像頭畫面</h3>
                            <div class="auraface-video-wrapper">
                                <video class="auraface-video" id="auraface-video" autoplay muted></video>
                                <canvas class="auraface-overlay" id="auraface-overlay"></canvas>
                            </div>
                        </div>
                        <div class="auraface-video-section">
                            <h3>即時統計</h3>
                            <div class="auraface-realtime-stats" id="auraface-realtime-stats">
                                等待識別結果...
                            </div>
                        </div>
                    </div>
                    
                    <div class="auraface-info" id="auraface-info">等待識別結果...</div>
                </div>
                
                ${this.config.enableStats ? `
                <div class="auraface-container">
                    <h2>📊 累計統計</h2>
                    <div class="auraface-stats-grid">
                        <div class="auraface-stat-card">
                            <div class="auraface-stat-number" id="auraface-total-frames">0</div>
                            <div>總處理幀數</div>
                        </div>
                        <div class="auraface-stat-card">
                            <div class="auraface-stat-number" id="auraface-faces-detected">0</div>
                            <div>檢測到人臉</div>
                        </div>
                        <div class="auraface-stat-card">
                            <div class="auraface-stat-number" id="auraface-employees-detected">0</div>
                            <div>員工識別</div>
                        </div>
                        <div class="auraface-stat-card">
                            <div class="auraface-stat-number" id="auraface-unknown-detected">0</div>
                            <div>未知人員</div>
                        </div>
                    </div>
                </div>
                ` : ''}
                
                ${this.config.enableRegistration ? `
                <div class="auraface-container">
                    <h2>👤 註冊新人臉</h2>
                    <div class="auraface-register-form">
                        <div class="auraface-form-group">
                            <label class="auraface-label" for="auraface-person-name">姓名：</label>
                            <input class="auraface-input" type="text" id="auraface-person-name" placeholder="請輸入姓名">
                        </div>
                        <div class="auraface-form-group">
                            <label class="auraface-label" for="auraface-person-role">身分：</label>
                            <select class="auraface-select" id="auraface-person-role">
                                <option value="員工">員工</option>
                                <option value="訪客">訪客</option>
                            </select>
                        </div>
                        <div class="auraface-form-group">
                            <label class="auraface-label" for="auraface-person-dept">部門：</label>
                            <input class="auraface-input" type="text" id="auraface-person-dept" placeholder="請輸入部門（可選）">
                        </div>
                        <button class="auraface-btn success" id="auraface-capture-register">拍照註冊</button>
                    </div>
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
            realtimeStats: container.querySelector('#auraface-realtime-stats'),
            startBtn: container.querySelector('#auraface-start-camera'),
            stopBtn: container.querySelector('#auraface-stop-camera'),
            connectBtn: container.querySelector('#auraface-connect')
        };
        
        // 統計元素
        if (this.config.enableStats) {
            this.elements.totalFrames = container.querySelector('#auraface-total-frames');
            this.elements.facesDetected = container.querySelector('#auraface-faces-detected');
            this.elements.employeesDetected = container.querySelector('#auraface-employees-detected');
            this.elements.unknownDetected = container.querySelector('#auraface-unknown-detected');
        }
        
        // 註冊元素
        if (this.config.enableRegistration) {
            this.elements.personName = container.querySelector('#auraface-person-name');
            this.elements.personRole = container.querySelector('#auraface-person-role');
            this.elements.personDept = container.querySelector('#auraface-person-dept');
            this.elements.captureBtn = container.querySelector('#auraface-capture-register');
            this.elements.captureBtn.addEventListener('click', () => this.registerFace());
        }
        
        this.overlayCtx = this.elements.overlay.getContext('2d');
        
        // 綁定事件
        this.elements.startBtn.addEventListener('click', () => this.startCamera());
        this.elements.stopBtn.addEventListener('click', () => this.stopCamera());
        this.elements.connectBtn.addEventListener('click', () => this.connect());
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
        this.isProcessing = false;
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
            
            let dynamicInterval = interval;
            if (this.delayStats.avgDelay > 800) {
                dynamicInterval = interval * 2;
            } else if (this.delayStats.avgDelay > 500) {
                dynamicInterval = interval * 1.5;
            }
            
            if (!this.isProcessing && 
                timeSinceLastSend >= dynamicInterval &&
                this.elements.video.videoWidth > 0 && 
                this.elements.video.videoHeight > 0) {
                
                if (this.isProcessing && timeSinceLastSend > 2000) {
                    console.warn('⚠️ 強制重置處理狀態 (超過2秒無回應)');
                    this.isProcessing = false;
                }
                
                this.sendFrame();
                this.lastSendTime = now;
            }
        }, Math.min(interval, 100));
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
        
        this.isProcessing = true;
        
        const canvas = document.createElement('canvas');
        canvas.width = Math.min(this.elements.video.videoWidth, 480);
        canvas.height = Math.min(this.elements.video.videoHeight, 360);
        
        const ctx = canvas.getContext('2d');
        // 發送原始影像數據，不翻轉
        ctx.drawImage(this.elements.video, 0, 0, canvas.width, canvas.height);
        
        const imageData = canvas.toDataURL('image/jpeg', 0.5);
        
        const clientTimestamp = Date.now();
        try {
            this.websocket.send(JSON.stringify({
                type: 'video_frame',
                image: imageData,
                client_timestamp: clientTimestamp
            }));
        } catch (error) {
            this.isProcessing = false;
            this.callbacks.onError(error);
        }
    }
    
    registerFace() {
        if (!this.config.enableRegistration) return;
        
        const name = this.elements.personName.value.trim();
        const role = this.elements.personRole.value;
        const dept = this.elements.personDept.value.trim();
        
        if (!name) {
            alert('請輸入姓名');
            return;
        }
        
        if (!this.elements.video.videoWidth || !this.elements.video.videoHeight) {
            alert('請先啟動攝像頭');
            return;
        }
        
        if (!this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
            alert('請先連接 WebSocket');
            return;
        }
        
        const canvas = document.createElement('canvas');
        canvas.width = this.elements.video.videoWidth;
        canvas.height = this.elements.video.videoHeight;
        
        const ctx = canvas.getContext('2d');
        // 註冊時也不翻轉，發送原始影像
        ctx.drawImage(this.elements.video, 0, 0);
        
        const imageData = canvas.toDataURL('image/jpeg', 0.9);
        
        try {
            this.websocket.send(JSON.stringify({
                type: 'register_face',
                name: name,
                role: role,
                department: dept,
                image: imageData
            }));
            
            this.elements.personName.value = '';
            this.elements.personDept.value = '';
            
            this.showNotification('註冊請求已發送，請等待處理結果...', 'info');
        } catch (error) {
            this.callbacks.onError(error);
        }
    }
    
    requestStats() {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(JSON.stringify({ type: 'get_stats' }));
        }
    }
    
    handleMessage(data) {
        switch (data.type) {
            case 'recognition_result':
                this.isProcessing = false;
                
                if (data.client_timestamp) {
                    const delay = Date.now() - data.client_timestamp;
                    this.updateDelayStats(delay);
                }
                
                this.displayResult(data);
                this.callbacks.onFaceDetected(data);
                break;
            case 'stats':
                this.updateStats(data.data);
                break;
            case 'register_result':
                this.handleRegistrationResult(data);
                break;
            case 'register_info':
                this.showNotification(data.message, 'info');
                break;
            case 'connection_status':
                break;
            case 'error':
                this.isProcessing = false;
                this.callbacks.onError(new Error(data.message));
                break;
        }
    }
    
    updateDelayStats(delay) {
        this.delayStats.recentDelays.push(delay);
        
        if (this.delayStats.recentDelays.length > 20) {
            this.delayStats.recentDelays.shift();
        }
        
        this.delayStats.maxDelay = Math.max(this.delayStats.maxDelay, delay);
        this.delayStats.avgDelay = this.delayStats.recentDelays.reduce((a, b) => a + b, 0) / this.delayStats.recentDelays.length;
        
        if (delay > 1000) {
            this.delayStats.warningCount++;
            console.warn(`🐌 延遲警告: ${delay}ms (平均: ${this.delayStats.avgDelay.toFixed(0)}ms, 最大: ${this.delayStats.maxDelay}ms)`);
            
            if (this.delayStats.warningCount >= 3) {
                console.warn('🔄 延遲過高，嘗試重置連接...');
                this.resetConnection();
            }
        } else if (delay < 500) {
            this.delayStats.warningCount = Math.max(0, this.delayStats.warningCount - 1);
        }
    }
    
    resetConnection() {
        console.log('🔄 重置 WebSocket 連接...');
        this.stopStreaming();
        this.disconnect();
        
        this.delayStats = {
            recentDelays: [],
            maxDelay: 0,
            avgDelay: 0,
            warningCount: 0
        };
        
        setTimeout(() => {
            this.connect();
            if (this.videoStream) {
                setTimeout(() => this.startStreaming(), 1000);
            }
        }, 2000);
    }
    
    displayResult(data) {
        // 更新資訊
        const info = `
FPS: ${data.fps} | 處理時間: ${data.processing_time}ms
檢測到 ${data.faces.length} 張人臉
${data.faces.map(face => 
    face.person_id !== 'unknown' ? 
        `✅ ${face.name} (${face.role}) - 信心度: ${face.confidence.toFixed(3)}` : 
        '❓ 未知人員'
).join('\\n')}
時間: ${data.timestamp}
        `;
        this.elements.info.textContent = info;
        
        // 更新即時統計
        const statsHtml = `
            <div><strong>即時識別結果</strong></div>
            <div>FPS: ${data.fps}</div>
            <div>處理時間: ${data.processing_time}ms</div>
            <div>檢測人臉: ${data.faces.length}</div>
            <div>已識別: ${data.faces.filter(f => f.person_id !== 'unknown').length}</div>
            <div>未知: ${data.faces.filter(f => f.person_id === 'unknown').length}</div>
            <hr>
            ${data.faces.map(face => 
                `<div style="margin: 5px 0; padding: 5px; background: ${face.person_id === 'unknown' ? '#ffebee' : '#e8f5e8'}; border-radius: 3px;">
                    ${face.person_id === 'unknown' ? '❓ 未知人員' : 
                      `✅ ${face.name}<br><small>${face.role} | 信心度: ${face.confidence.toFixed(3)}</small>`}
                </div>`
            ).join('')}
        `;
        this.elements.realtimeStats.innerHTML = statsHtml;
        
        // 儲存並繪製
        this.currentFaces = data.faces;
        this.drawFaceOverlay();
    }
    
    updateStats(stats) {
        if (!this.config.enableStats) return;
        
        this.globalStats = stats;
        
        if (this.elements.totalFrames) this.elements.totalFrames.textContent = stats.total_frames;
        if (this.elements.facesDetected) this.elements.facesDetected.textContent = stats.faces_detected;
        if (this.elements.employeesDetected) this.elements.employeesDetected.textContent = stats.employees_detected;
        if (this.elements.unknownDetected) this.elements.unknownDetected.textContent = stats.unknown_detected;
    }
    
    handleRegistrationResult(data) {
        const message = data.success ? '✅ 註冊成功: ' + data.message : '❌ 註冊失敗: ' + data.message;
        this.showNotification(message, data.success ? 'success' : 'error');
        this.callbacks.onRegistrationComplete(data);
    }
    
    showNotification(message, type = 'info') {
        const colors = {
            info: '#17a2b8',
            success: '#28a745', 
            error: '#dc3545'
        };
        
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: ${colors[type]};
            color: white;
            padding: 15px;
            border-radius: 5px;
            z-index: 1000;
            max-width: 300px;
            word-wrap: break-word;
        `;
        notification.textContent = message;
        
        document.body.appendChild(notification);
        setTimeout(() => notification.remove(), 5000);
    }
    
    drawFaceOverlay() {
        if (!this.elements.video.videoWidth || !this.elements.video.videoHeight) return;
        
        // 設置畫布大小為影片顯示大小（而非原始大小）
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
                color = '#ff0000';
            } else if (face.role === '員工') {
                color = '#00ff00';
            } else if (face.role === '訪客') {
                color = '#ffff00';
            } else {
                color = '#ff0000';
            }
            
            this.overlayCtx.strokeStyle = color;
            this.overlayCtx.lineWidth = 2;
            this.overlayCtx.strokeRect(displayX1, displayY1, displayX2 - displayX1, displayY2 - displayY1);
            
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
    
    getGlobalStats() {
        return { ...this.globalStats };
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
    module.exports = AuraFaceAdmin;
} else if (typeof define === 'function' && define.amd) {
    define([], function() { return AuraFaceAdmin; });
} else {
    window.AuraFaceAdmin = AuraFaceAdmin;
}