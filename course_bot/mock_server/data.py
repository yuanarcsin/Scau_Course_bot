"""
模拟数据 —— 基于真实 CDP 抓包结果构造。
"""

import copy

# 固定 RSA 密钥对（测试用，不用于真实加密）
MOCK_RSA_MODULUS = (
    "AKRB6FwmOe0hE9Uo6LMKoDE5U9JU9lH1v8Uv7ATjRj2W"
    "+aTPlR9Hfm8fR782pzGwDsTD4Yr7tBHQ1cuEnGrqrJn5"
    "HuPiLqmSg4Z/AwS+Rq8eE7T+ZaGoUtpqvcoSffSJOW29"
    "RNVMwT391ona/+eK5B3RkC9WaJFYiZai7FiQDeXT"
)
MOCK_RSA_EXPONENT = "AQAB"

# 登录页 CSRF token（固定）
MOCK_CSRF = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"

# 页面隐藏参数（精简关键字段）
PAGE_PARAMS = {
    "xkxnm": "2026", "xkxnmc": "2026", "xkxqmc": "第一学期",
    "xkxqm": "3", "xklc": "1", "xklcmc": "第一轮",
    "rwlx": "3", "rlkz": "0", "rlzlkz": "1", "cdrlkz": "0",
    "xszxzt": "1", "xqh_id": "3", "jg_id": "14", "jg_id_1": "14",
    "njdm_id": "2025", "zyh_id": "W134d8ed0002bJ",
    "zyfx_id": "wfx", "bh_id": "539616",
    "bklx_id": "523C417905131A3FE06322F8A8C06F6F",
    "firstKklxdm": "06",
    "firstXkkzId": "53E20EFA9F06504FE06321F8A8C069E5",
    "firstXkkzXh": "1",
    "firstNjdmId": "2025",
    "firstZyhId": "W134d8ed0002bJ",
    "xkkz_id": "53E20EFA9F06504FE06321F8A8C069E5",
    "xkkssj": "2026-06-10 12:30:00",
    "xkjssj": "2026-06-22 23:59:59",
    "server_now": "2026-06-18 12:29:00",
    "currentsj": "2026-06-18 12:29:00",
    "sfkkjyxdxnxq": "0", "kzkcgs": "0", "xkly": "0",
    "gnjkxdnj": "0", "bjgkczxbbjwcx": "0",
    "xbm": "1", "xslbdm": "1", "mzm": "01", "xz": "4", "ccdm": "1",
    "xsbj": "0", "sfkknj": "0", "sfkkzy": "0", "kzybkxy": "0",
    "sfznkx": "0", "zdkxms": "0", "sfkxq": "1", "sfkcfx": "0",
    "kkbk": "0", "kkbkdj": "0", "bklbkcj": "0",
    "sfkgbcx": "0", "sfrxtgkcxd": "0", "tykczgxdcs": "0",
    "bbhzxjxb": "0", "qz": "0", "xxkbj": "0", "cxbj": "0",
    "sfyjxk": "1", "sfyxsksjct": "0", "sfqzxk": "0",
    "xsckxkgzkg": "0", "zzxkxfmcjckg": "0", "xsckgrkbkg": "0",
    "txbsfrl": "0", "kxqxktskg": "0",
    "zxfs": "0",
}

# tab 信息
TABS = {
    "06": {
        "kklxdm": "06",
        "xkkz_id": "53E20EFA9F06504FE06321F8A8C069E5",
        "njdm_id": "2025",
        "zyh_id": "W134d8ed0002bJ",
        "xkkz_xh": "1",
    },
}

# 模拟课程数据（基于真实 PartDisplay 响应格式）
COURSES = [
    {
        "jxb_id": "524B8FD8F06D77F8E06321F8A8C09A75",
        "do_jxb_id": "150d3906294749b2200c0ccf2784e1e4cec632f4136adc7c25091d2019462450c5ca0b759659e0c23072fefe58c03a659cd5b83b5c16163fe57fa98bdac1587994bd1663dad588993fca4ba438c735e705edc4370f3e2ed61b443926b8bca70601efd1141f242992333c0d0236f38c3900c320dbf77ef3ad51b0895fdf71f978",
        "kch_id": "511D01F9860E5B7EE06321F8A8C02334",
        "kch": "604794",
        "kcmc": "大学英语Ⅲ (翻译)",
        "jxbmc": "202620271-604792-005",
        "jxbzls": "1",
        "jxbxf": "2.0",
        "kklxdm": "06",
        "kzmc": "大学英语Ⅲ模块",
        "yxzrs": "30",
        "jxbrs": "52",
        "blyxrs": "0",
        "blzyl": "0",
        "rwzxs": "32",
        "kclxmc": "普通课",
        "jsxx": "30001885/李志英/讲师",
        "jxdd": "5D507",
        "xf": "2.0",
        "jgpxzd": "1",
        "cxbj": "0",
        "fxbj": "0",
        "xxkbj": "0",
        "zcongbj": "0",
        "kcrow": "1",
        "listnav": "false",
    },
    {
        "jxb_id": "5196FCEF7165993CE06321F8A8C0FFDC",
        "do_jxb_id": "50ce3ceda10cfb734c0f4c976a8638a4714053b94ac7a7d5e412b42a69209d28672d623433b25966f21163f244cfb0ed3b550a5fed63f717229b7140243357c594225def6849dec075088cf528473f0037a7c10abd860d8292fc6bc8344eadd261930902e93eafba520ca23d5022271c274544adc67e152d6864b478b1dd139b",
        "kch_id": "511D01F9860E5B7EE06321F8A8C02335",
        "kch": "610023",
        "kcmc": "乒乓球",
        "jxbmc": "202620271-610023-001-乒乓球02",
        "jxbzls": "1",
        "jxbxf": "1.0",
        "kklxdm": "06",
        "kzmc": "体育Ⅲ项目",
        "yxzrs": "45",
        "jxbrs": "48",
        "blyxrs": "0",
        "blzyl": "0",
        "rwzxs": "32",
        "kclxmc": "普通课",
        "jsxx": "000000/待定/无",
        "jxdd": "运动场",
        "xf": "1.0",
        "jgpxzd": "1",
        "cxbj": "0",
        "fxbj": "0",
        "xxkbj": "0",
        "zcongbj": "0",
        "kcrow": "1",
        "listnav": "false",
    },
]

_ORIGINAL_COURSES = copy.deepcopy(COURSES)


def reset_courses():
    """重置 COURSES 到初始状态（测试间隔离用）"""
    global COURSES
    COURSES[:] = copy.deepcopy(_ORIGINAL_COURSES)
