"""
ComfyUI-DMXAPI 節點註冊

新增節點模組時，除了在該模組內定義 NODE_CLASS_MAPPINGS 與
NODE_DISPLAY_NAME_MAPPINGS，務必一併加進下方的 _MODULES，否則節點不會載入。
"""

from . import dmxapi_gpt_image2_node
from . import dmxapi_minimax_h3_nodes
from . import dmxapi_seedance2

_MODULES = [
    dmxapi_gpt_image2_node,
    dmxapi_minimax_h3_nodes,
    dmxapi_seedance2,
]

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

for _module in _MODULES:
    for _key in _module.NODE_CLASS_MAPPINGS:
        if _key in NODE_CLASS_MAPPINGS:
            raise RuntimeError(
                "[DMXAPI Error] 節點 ID 重複註冊：" + _key
                + "（來自 " + _module.__name__ + "）"
            )
    NODE_CLASS_MAPPINGS.update(_module.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(_module.NODE_DISPLAY_NAME_MAPPINGS)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
