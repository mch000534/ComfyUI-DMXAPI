import { app } from "../../scripts/app.js";

// 把 DMXAPI_SetAPIKey 節點的 api_key 欄位遮蔽成密碼輸入框，並在執行成功後自動清空。
//
// 這只防止「看畫面的人」偷看到明碼，不能解決存檔外洩風險：欄位的值在執行當下
// 仍會以明文送進 workflow prompt JSON；自動清空只防得住「執行完之後才存檔/分享」
// 的情況，如果使用者填了值、還沒執行就存檔，明碼一樣會進去。
app.registerExtension({
    name: "DMXAPI.SetAPIKey.Mask",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "DMXAPI_SetAPIKey") {
            return;
        }

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            const widget = this.widgets?.find((w) => w.name === "api_key");
            if (widget?.inputEl) {
                widget.inputEl.type = "password";
            }
            return result;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = onExecuted?.apply(this, arguments);
            const widget = this.widgets?.find((w) => w.name === "api_key");
            if (widget) {
                widget.value = "";
                if (widget.inputEl) {
                    widget.inputEl.value = "";
                }
            }
            return result;
        };
    },
});

// 設定畫面裡的第二個入口：跟上面的節點共用同一個後端（dmxapi_server_routes.py
// 的 /dmxapi/api_key），效果完全一致。跟節點不同的是，這裡的值存在 ComfyUI
// 自己的使用者設定檔（user/default/comfy.settings.json），不會被存進 workflow
// JSON、也不會被嵌進輸出圖片的 metadata——是比節點更安全的入口。
//
// category 刻意指定成 ["DMXAPI", ...]：不指定才會落到 ComfyUI 預設的「其他」
// 分類，指定了就會有一個獨立、好找的「DMXAPI」分類。
app.registerExtension({
    name: "DMXAPI.SetAPIKey.Settings",
    settings: [
        {
            id: "DMXAPI.ApiKey",
            name: "DMXAPI_KEY",
            category: ["DMXAPI", "API Key", "DMXAPI_KEY"],
            type: "text",
            defaultValue: "",
            tooltip:
                "留空並儲存＝不變更；輸入新值＝覆蓋 .env 的 DMXAPI_KEY。" +
                "這裡的值只會送到本機的 .env，不會存進 workflow 或圖片 metadata。",
            onChange: async (value) => {
                if (!value || !value.trim()) {
                    return;
                }
                try {
                    const resp = await fetch("/dmxapi/api_key", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ value }),
                    });
                    if (!resp.ok) {
                        throw new Error(await resp.text());
                    }
                } catch (err) {
                    console.error("[DMXAPI] 寫入 API Key 失敗：", err);
                }
            },
        },
    ],
    async setup() {
        // 嘗試把目前已設定 Key 的遮蔽片段顯示出來。這裡先印到 console；動態塞進
        // 畫面欄位的 tooltip/placeholder 取決於這版設定框架有沒有開放對應 API，
        // 需要實機在設定畫面驗證後再決定要不要進一步串接。
        try {
            const resp = await fetch("/dmxapi/api_key");
            const data = await resp.json();
            console.log("[DMXAPI] 目前已設定的 API Key：", data.masked ?? "（尚未設定）");
        } catch (err) {
            console.warn("[DMXAPI] 無法讀取目前的 API Key 狀態：", err);
        }
    },
});
