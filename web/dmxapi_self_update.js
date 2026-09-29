import { app } from "../../scripts/app.js";

// 在 ComfyUI 設定畫面加入「檢查更新」／「套用更新」，對應後端
// dmxapi_server_routes.py 的 /dmxapi/self_update/check（GET）與
// /dmxapi/self_update/apply（POST），跟 DMXAPI_SelfUpdate 節點共用同一套邏輯。
//
// 這裡用 boolean 開關模擬「按鈕」：切成 true 才觸發動作，動作結束後（不論成功
// 失敗）都會把開關撥回 false，避免使用者以為這是一個會持續生效的設定。
// ComfyUI 這版設定框架有沒有更適合的原生「按鈕」型別，這個目錄裡沒有前端原始碼
// 可以核對，需要實機在瀏覽器打開設定畫面驗證。
app.registerExtension({
    name: "DMXAPI.SelfUpdate.Settings",
    settings: [
        {
            id: "DMXAPI.CheckForUpdate",
            name: "檢查更新",
            category: ["DMXAPI", "自我更新", "檢查更新"],
            type: "boolean",
            defaultValue: false,
            tooltip: "切成開啟＝立即比對本機與 GitHub main 分支的最新版本，絕不會修改任何檔案。結果會用彈出視窗顯示。",
            onChange: async (value) => {
                if (!value) {
                    return;
                }
                try {
                    const resp = await fetch("/dmxapi/self_update/check");
                    const data = await resp.json();
                    alert(data.report ?? "[DMXAPI] 檢查更新失敗，請查看 ComfyUI 主控台日誌。");
                } catch (err) {
                    console.error("[DMXAPI] 檢查更新失敗：", err);
                    alert("[DMXAPI] 檢查更新失敗：" + err);
                } finally {
                    resetToggle("DMXAPI.CheckForUpdate");
                }
            },
        },
        {
            id: "DMXAPI.ApplyUpdate",
            name: "套用更新",
            category: ["DMXAPI", "自我更新", "套用更新"],
            type: "boolean",
            defaultValue: false,
            tooltip: "切成開啟＝下載並套用 GitHub 最新版本（會保留 .env 與 .git）。" +
                      "套用後需要重新啟動 ComfyUI 才會生效。這個動作會覆蓋套件程式碼，執行前會再跳出一次確認。",
            onChange: async (value) => {
                if (!value) {
                    return;
                }
                if (!confirm(
                    "確定要下載並套用 ComfyUI-DMXAPI 的最新版本嗎？\n\n" +
                    "會保留現有的 .env 與 .git，但仍會覆蓋套件的程式碼檔案，" +
                    "且需要重新啟動 ComfyUI 才會生效。"
                )) {
                    resetToggle("DMXAPI.ApplyUpdate");
                    return;
                }
                try {
                    const resp = await fetch("/dmxapi/self_update/apply", { method: "POST" });
                    const data = await resp.json();
                    alert(data.report ?? "[DMXAPI] 套用更新失敗，請查看 ComfyUI 主控台日誌。");
                } catch (err) {
                    console.error("[DMXAPI] 套用更新失敗：", err);
                    alert("[DMXAPI] 套用更新失敗：" + err);
                } finally {
                    resetToggle("DMXAPI.ApplyUpdate");
                }
            },
        },
    ],
});

// 把開關撥回關閉，讓它單純只是一次性的觸發，不是持續生效的設定。
// app.ui.settings 是舊版 API 名稱，實際可用的方法要在瀏覽器裡打開設定畫面後
// 才能確認；這裡包 try/catch，就算 API 對不上也不影響檢查/套用本身有沒有成功。
function resetToggle(id) {
    try {
        app.ui.settings.setSettingValue(id, false);
    } catch (err) {
        console.warn("[DMXAPI] 無法把設定開關重設回關閉狀態：", err);
    }
}
