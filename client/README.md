# AuraFace 客戶端架構說明

## 📁 檔案結構

```
client/
├── sdk/
│   ├── auraface-viewer.js    # 🔒 只讀功能 SDK（第三方使用）
│   └── auraface-admin.js     # 🔑 完整管理功能 SDK（管理員使用）
├── viewer.html               # 📱 第三方整合範例
├── admin-sdk.html           # 👨‍💼 管理員 SDK 版本
├── admin.html               # 📋 管理員原始版本（保留）
└── README.md               # 📖 本說明文件
```

## 🎯 快速開始

### 第三方整合（3 步驟）

1. **下載檔案**
   ```bash
   # 只需要這兩個檔案
   auraface-viewer.js
   viewer.html
   ```

2. **基本使用**
   ```html
   <!DOCTYPE html>
   <html>
   <body>
       <div id="face-recognition"></div>
       
       <script src="auraface-viewer.js"></script>
       <script>
           const viewer = new AuraFaceViewer({
               container: '#face-recognition'  // 就這麼簡單！
           });
       </script>
   </body>
   </html>
   ```

3. **自定義處理**
   ```javascript
   const viewer = new AuraFaceViewer({
       container: '#face-recognition',
       wsUrl: 'ws://your-server:7861',  // 改成你的伺服器
       onFaceDetected: function(data) {
           // 當識別到人臉時的處理
           data.faces.forEach(face => {
               if (face.person_id !== 'unknown') {
                   console.log(`識別到: ${face.name} (${face.role})`);
                   // 整合到你的系統中...
               }
           });
       }
   });
   ```

### 管理員使用（完整功能）

1. **下載檔案**
   ```bash
   # 管理員需要完整套件
   auraface-admin.js
   admin-sdk.html
   ```

2. **啟動管理介面**
   ```html
   <!DOCTYPE html>
   <html>
   <body>
       <div id="admin-panel"></div>
       
       <script src="auraface-admin.js"></script>
       <script>
           const admin = new AuraFaceAdmin({
               container: '#admin-panel',
               enableRegistration: true,  // 啟用註冊功能
               enableStats: true          // 啟用統計功能
           });
       </script>
   </body>
   </html>
   ```

## 🔐 權限說明

### 第三方獲得的功能
**檔案**: `auraface-viewer.js`

- ✅ **即時人臉識別顯示** - 可以看到識別結果
- ✅ **攝像頭控制** - 啟動/停止攝像頭
- ✅ **連接狀態監控** - 監控 WebSocket 連接
- ✅ **基本統計資料** - FPS、處理時間等
- ❌ **無法註冊新人臉** - 保護敏感功能
- ❌ **無法存取管理功能** - 無法看到系統管理代碼

### 管理員獲得的功能  
**檔案**: `auraface-admin.js`

- ✅ **完整人臉識別功能** - 所有識別功能
- ✅ **人臉註冊功能** - 註冊新員工/訪客（支援多人場景）
- ✅ **詳細統計資料** - 累計統計、即時數據
- ✅ **系統監控功能** - 連接監控、錯誤處理
- ✅ **管理員專用介面** - 完整的管理控制台

## SDK 配置選項

### AuraFaceViewer 配置
```javascript
{
    wsUrl: 'ws://localhost:7861',    // WebSocket 伺服器地址
    container: document.body,        // 容器元素
    autoConnect: true,               // 自動連接
    showStats: true,                 // 顯示統計資料
    frameRate: 5,                    // 影片幀率
    onConnected: function(event) {}, // 連接成功回調
    onDisconnected: function(event) {}, // 斷線回調
    onFaceDetected: function(data) {}, // 人臉檢測回調
    onError: function(error) {}      // 錯誤回調
}
```

### AuraFaceAdmin 配置
```javascript
{
    wsUrl: 'ws://localhost:7861',         // WebSocket 伺服器地址
    container: document.body,             // 容器元素
    autoConnect: true,                    // 自動連接
    enableRegistration: true,             // 啟用註冊功能
    enableStats: true,                    // 啟用統計功能
    frameRate: 5,                         // 影片幀率
    onConnected: function(event) {},      // 連接成功回調
    onDisconnected: function(event) {},   // 斷線回調
    onFaceDetected: function(data) {},    // 人臉檢測回調
    onRegistrationComplete: function(data) {}, // 註冊完成回調
    onError: function(error) {}           // 錯誤回調
}
```

## API 方法

### 通用方法（兩個 SDK 都有）
- `connect()` - 手動連接 WebSocket
- `disconnect()` - 斷開連接
- `startCamera()` - 啟動攝像頭
- `stopCamera()` - 停止攝像頭
- `getConnectionStatus()` - 獲取連接狀態
- `getCurrentFaces()` - 獲取當前檢測到的人臉
- `destroy()` - 銷毀實例

### 管理員專用方法（僅 Admin SDK）
- `registerFace()` - 註冊人臉
- `requestStats()` - 請求統計資料
- `getGlobalStats()` - 獲取累計統計
- `showNotification(message, type)` - 顯示通知

## 安全考量

### 權限分離
- **Viewer SDK**: 第三方只能獲得識別結果，無法看到註冊功能代碼
- **Admin SDK**: 管理員獲得完整功能，包含敏感操作

### 資料保護
- 第三方無法訪問人臉註冊介面
- 統計資料可選擇性提供
- 錯誤資訊不包含敏感系統資訊

### 部署建議
1. **生產環境**: 只部署 `viewer.html` 和 `auraface-viewer.js` 給第三方
2. **管理環境**: 在安全網路內部署完整的 Admin SDK
3. **API Token**: 可考慮在伺服器端加入 token 驗證機制

## 💡 實際使用範例

### 考勤系統整合
```html
<!DOCTYPE html>
<html>
<head>
    <title>員工考勤系統</title>
</head>
<body>
    <h1>智能考勤打卡</h1>
    <div id="attendance-system"></div>
    
    <script src="auraface-viewer.js"></script>
    <script>
        const attendanceSystem = new AuraFaceViewer({
            container: '#attendance-system',
            wsUrl: 'ws://attendance-server:7861',
            onFaceDetected: function(data) {
                data.faces.forEach(face => {
                    if (face.person_id !== 'unknown') {
                        // 記錄打卡
                        recordAttendance(face.name, face.role);
                        showWelcomeMessage(face.name);
                    } else {
                        showUnknownPersonAlert();
                    }
                });
            }
        });
        
        function recordAttendance(name, role) {
            console.log(`${name} (${role}) 已打卡 - ${new Date().toLocaleString()}`);
            // 發送到後端系統...
        }
        
        function showWelcomeMessage(name) {
            alert(`歡迎 ${name}！`);
        }
        
        function showUnknownPersonAlert() {
            console.log('檢測到未知人員，請聯繫管理員');
        }
    </script>
</body>
</html>
```

### 安全監控系統
```javascript
// 門禁安全系統
const securitySystem = new AuraFaceViewer({
    container: '#security-monitor',
    wsUrl: 'wss://secure-building.com:7861',
    frameRate: 2,  // 降低頻率節省頻寬
    
    onFaceDetected: function(data) {
        const currentTime = new Date().toLocaleString();
        
        data.faces.forEach(face => {
            if (face.person_id !== 'unknown') {
                // 已授權人員
                logSecurityEvent('AUTHORIZED_ENTRY', {
                    person: face.name,
                    role: face.role,
                    confidence: face.confidence,
                    timestamp: currentTime
                });
                
                if (face.role === '員工') {
                    grantAccess(face.name);
                }
            } else {
                // 未授權人員 - 安全警告
                logSecurityEvent('UNAUTHORIZED_DETECTION', {
                    timestamp: currentTime,
                    action: 'ALERT_SECURITY'
                });
                
                triggerSecurityAlert();
            }
        });
    },
    
    onError: function(error) {
        // 系統錯誤也要記錄
        logSecurityEvent('SYSTEM_ERROR', {
            error: error.message,
            timestamp: new Date().toLocaleString()
        });
    }
});

function logSecurityEvent(type, data) {
    console.log(`[SECURITY] ${type}:`, data);
    // 發送到安全監控後端...
}

function grantAccess(personName) {
    console.log(`✅ 授權通過: ${personName}`);
    // 開啟門禁、記錄進入時間等...
}

function triggerSecurityAlert() {
    console.log('🚨 安全警告：檢測到未授權人員');
    // 觸發警報、通知保全等...
}
```

### 零售店客戶分析
```javascript
// 智能零售分析
const retailAnalytics = new AuraFaceViewer({
    container: '#customer-analytics',
    wsUrl: 'ws://retail-ai.com:7861',
    showStats: true,  // 顯示統計幫助分析
    
    onFaceDetected: function(data) {
        // 客戶進店分析
        if (data.faces.length > 0) {
            updateCustomerCount(data.faces.length);
            
            data.faces.forEach(face => {
                if (face.person_id !== 'unknown') {
                    // VIP 客戶識別
                    handleVIPCustomer(face);
                } else {
                    // 新客戶
                    handleNewCustomer();
                }
            });
        }
    }
});

function updateCustomerCount(count) {
    document.getElementById('live-customer-count').textContent = count;
}

function handleVIPCustomer(customer) {
    console.log(`🌟 VIP 客戶進店: ${customer.name}`);
    // 顯示個人化推薦、通知店員等...
    showPersonalizedOffers(customer.name);
}

function handleNewCustomer() {
    console.log('👋 新客戶進店');
    // 顯示歡迎訊息、新客優惠等...
    showWelcomeOffer();
}
```