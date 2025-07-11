# AuraFace API 文檔

## 📋 API 端點總覽

**Base URL**: `http://localhost:7859`  
**Swagger 文檔**: `http://localhost:7859/docs`

### 1. 健康檢查
```bash
GET /api/health
```

**用途**: 檢查 API 服務與資料庫連接狀態

**範例**:
```bash
curl "http://localhost:7859/api/health"
```

**回傳**:
```json
{
  "success": true,
  "status": "healthy",
  "database": "connected",
  "timestamp": "2025-07-11T09:42:45.201575+08:00"
}
```

### 2. 出勤記錄查詢
```bash
GET /api/attendance
```

**參數**:
- `person_id` (可選): 人員ID
- `name` (可選): 姓名模糊查詢
- `minutes` (可選): 最近N分鐘內有活動的記錄 (基於最後出現時間)，**預設10分鐘**，設為0表示不限制時間
- `limit` (可選): 查詢數量限制，預設50，最大200

**查詢範例**:

```bash
# 獲取最近10分鐘內有活動的記錄 (預設行為)
curl "http://localhost:7859/api/attendance"

# 獲取所有記錄 (不限制時間)
curl "http://localhost:7859/api/attendance?minutes=0"

# 限制查詢數量
curl "http://localhost:7859/api/attendance?limit=10"

# 按人員ID查詢 (仍套用10分鐘預設)
curl "http://localhost:7859/api/attendance?person_id=員工_0001_81c46bc1"

# 按姓名模糊查詢 (仍套用10分鐘預設)
curl "http://localhost:7859/api/attendance?name=CSL"

# 最近1小時內有活動的記錄
curl "http://localhost:7859/api/attendance?minutes=60"

# 組合查詢：CSL在最近30分鐘內的記錄
curl "http://localhost:7859/api/attendance?name=CSL&minutes=30&limit=5"

# 獲取CSL的所有歷史記錄
curl "http://localhost:7859/api/attendance?name=CSL&minutes=0&limit=100"
```

**回傳格式**:
```json
{
  "success": true,
  "data": [
    {
      "session_uuid": "c3779c63-010f-40a2-8e24-0aa0b13650a8",
      "person_id": "員工_0001_81c46bc1",
      "name": "CSL",
      "department": "研發部",
      "role": "員工",
      "employee_id": "1106",
      "email": "csl426@aicreate360.com",
      "status": "active",
      "status_text": "在席中",
      "arrival_time": "2025-07-11T09:39:57.233294+08:00",
      "arrival_time_formatted": "2025-07-11 09:39:57",
      "departure_time": null,
      "departure_time_formatted": "",
      "last_seen_at": "2025-07-11T09:40:46.267382+08:00",
      "last_seen_formatted": "2025-07-11 09:40:46",
      "duration_seconds": 177,
      "duration_text": "已持續 2分鐘"
    }
  ],
  "count": 1,
  "query": {
    "person_id": null,
    "name": null,
    "minutes": 10,
    "limit": 50
  },
  "generated_at": "2025-07-11T09:42:54.486077+08:00"
}
```

## 📊 欄位說明

### 會話資訊
- `session_uuid`: 出勤會話唯一識別碼 (每次出現都有不同的UUID)
- `person_id`: 系統生成的唯一人員ID

### 人員資訊
- `name`: 姓名
- `department`: 部門
- `role`: 角色 (員工/訪客)
- `employee_id`: 員工編號
- `email`: 電子郵件

### 出勤狀態
- `status`: 狀態代碼 (`active`=在席中, `ended`=已離開)
- `status_text`: 狀態文字描述

### 時間資訊
- `arrival_time`: 到達時間 (ISO 8601 格式)
- `arrival_time_formatted`: 格式化的到達時間
- `departure_time`: 離開時間 (若仍在席則為 null)
- `departure_time_formatted`: 格式化的離開時間
- `last_seen_at`: 最後出現時間
- `last_seen_formatted`: 格式化的最後出現時間

### 持續時間
- `duration_seconds`: 持續時間(秒)
- `duration_text`: 持續時間文字描述

## 🔧 程式整合範例

### JavaScript
```javascript
// 獲取出勤記錄
async function getAttendance(name = null, minutes = null, limit = 50) {
    const params = new URLSearchParams();
    if (name) params.append('name', name);
    if (minutes) params.append('minutes', minutes);
    if (limit) params.append('limit', limit);
    
    const response = await fetch(`http://localhost:7859/api/attendance?${params}`);
    const data = await response.json();
    return data;
}

// 使用範例
getAttendance('CSL', 30, 10).then(data => {
    console.log('CSL最近30分鐘的出勤記錄:', data.data);
});
```

### Python
```python
import requests

# 獲取出勤記錄
def get_attendance(name=None, person_id=None, minutes=None, limit=50):
    params = {'limit': limit}
    if name:
        params['name'] = name
    if person_id:
        params['person_id'] = person_id
    if minutes:
        params['minutes'] = minutes
    
    response = requests.get('http://localhost:7859/api/attendance', params=params)
    return response.json()

# 使用範例
data = get_attendance(name='CSL', minutes=30, limit=10)
print(f"CSL最近30分鐘內找到 {data['count']} 筆記錄")
```

### PHP
```php
<?php
function getAttendance($name = null, $personId = null, $limit = 50) {
    $params = ['limit' => $limit];
    if ($name) $params['name'] = $name;
    if ($personId) $params['person_id'] = $personId;
    
    $url = 'http://localhost:7859/api/attendance?' . http_build_query($params);
    $response = file_get_contents($url);
    return json_decode($response, true);
}

// 使用範例
$data = getAttendance('CSL', null, 10);
echo "找到 " . $data['count'] . " 筆記錄\n";
?>
```

## 🏗️ 架構說明

### 檔案結構
```
api/
├── standalone_api.py    # 主要 API 服務 (FastAPI)
├── attendance_api.py    # 出勤查詢函數 (供 Gradio 使用)
└── README.md           # 此文檔
```

### 服務啟動
- API 服務由 `app.py` 在背景執行緒中自動啟動
- 端口 7859 已在 `docker-compose.yml` 中暴露
- 直接連接 PostgreSQL 資料庫

### 資料庫連接
- 使用 `DATABASE_URL` 環境變數
- 預設: `postgresql://ai360:ai360@postgres:5432/auraface`
- 支援連接池和自動重連

## 🚨 錯誤處理

### HTTP 狀態碼
- `200`: 成功
- `422`: 參數驗證錯誤
- `500`: 伺服器內部錯誤

### 錯誤回應格式
```json
{
  "detail": "錯誤訊息"
}
```

## 🔍 偵錯

### 檢查 API 服務狀態
```bash
# 檢查服務是否運行
curl "http://localhost:7859/api/health"

# 檢查容器日誌
docker-compose logs auraface --tail 20
```

### 常見問題
1. **連接被拒絕**: 檢查 Docker 容器是否正常運行
2. **資料庫錯誤**: 檢查 PostgreSQL 容器狀態
3. **無數據返回**: 確認資料庫中有出勤記錄