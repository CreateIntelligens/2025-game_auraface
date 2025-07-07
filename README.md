# 🔍 AuraFace 人臉識別系統

商用人臉識別系統，支援 PostgreSQL + WebSocket 即時識別

## 🚀 快速啟動

```bash
git clone <repository>
cd auraface-similarity
docker compose up -d
```

**服務端點**：
- 網頁界面：http://localhost:7860
- WebSocket：ws://localhost:7861
- PostgreSQL：localhost:5432

## 📋 使用方式

### 1. 網頁界面
http://localhost:7860 - 人臉註冊、圖片識別、影片處理

### 2. WebSocket 程式接入
```javascript
const ws = new WebSocket('ws://localhost:7861');

// 發送圖片進行識別
ws.send(JSON.stringify({
  type: 'video_frame',
  image: 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQ...'
}));

// 接收識別結果
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'recognition_result') {
    console.log('識別結果:', data.faces);
    // data.faces: [{person_id, name, role, confidence, bbox}]
  }
};
```

### 3. 資料庫管理
- **網頁管理**：http://localhost:7860 → 「資料庫管理」標籤
- **直接連接**：
```bash
docker exec auraface-postgres psql -U auraface -d auraface
```

## 🏗️ 架構

**技術棧**：AuraFace + PostgreSQL + WebSocket + Docker

## 🔧 設定

**識別閾值調整**：修改 `app.py` 第156行的 `threshold=0.15` 參數

**資料庫連接**：`postgresql://auraface:auraface123@postgres:5432/auraface`

## 🚨 故障排除

```bash
# 檢查服務狀態
docker logs auraface-app
docker logs auraface-postgres

# 重啟服務
docker compose restart
```