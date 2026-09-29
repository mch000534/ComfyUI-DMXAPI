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
