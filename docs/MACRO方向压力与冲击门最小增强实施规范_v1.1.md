# MACRO 鏂瑰悜鍘嬪姏涓庡啿鍑婚棬鏈€灏忓寮哄疄鏂借鑼?v1.1

鏃ユ湡锛?026-06-25
閫傜敤鐗堟湰锛氶」鐩?r3.3.1锛孎MZ 淇″彿灞?`demo_version=1.5.1`
閫傜敤鏂囦欢锛歚demo/鏈€鏂颁氦浠樼墿/neutral_regulation_demo_fmz.py`

## 鐩爣

鏈疆鍙仛 MACRO 鏈€灏忓寮猴細灏?`macro_score / macro_regime` 鐨勬柟鍚戣儗鏅兘鍔涳紝涓?`macro_shock` 鐨勫啿鍑婚棬闃绘柇鑳藉姏鎷嗗紑銆?
绋冲畾楂橀€嗛涓嶅啀鑷姩纭樆鏂紱纭鐨?VOLQ 鍐插嚮璺冭縼骞跺緱鍒?DXY 鎴?US10Y 鍘嬪姏纭鏃讹紝鎵嶈緭鍑?`MACRO_SHOCK_BLOCKING`銆傛湰瑙勮寖涓嶄慨鏀规墽琛屽眰銆丟GR銆乂RP銆丒DB 鏉冮噸銆丩LM prompt 鎴?systemd 鏈嶅姟銆?
## Producer 鍘熺敓杈撳嚭

- `macro_score`銆乣macro_regime`銆乣components` 淇濇寔鍘熷惈涔夛紝缁х画浣滀负鏂瑰悜鑳屾櫙鍜屽璁?raw trace銆?- 鏂板 `macro_shock`锛?  - `state`: `CLEAR | WATCH | BLOCK | UNKNOWN`
  - `block`: `true | false`
  - `macro_score_delta`
  - `volq_bps_delta`
  - `headwind_threshold_crossed`
  - `direction_confirmed`
  - `reason_codes`
- 鏂板 `legacy_blocking_flags`锛氬彧璁板綍鏃у垎鏁伴樆鏂奖瀛愶紝涓嶈繘鍏ユ渶缁?hard veto銆?- `blocking_flags` 鍙厑璁告潵鑷?producer 鍘熺敓 `macro_shock.block`锛屽吀鍨嬪€间负 `MACRO_SHOCK_BLOCKING`銆?
## Previous Snapshot

涓婁竴浠芥湁鏁?MACRO 鎽樿浣跨敤鐜版湁 `macro_cache_file` 淇濆瓨锛屼笉鏂板鐙珛鏂囦欢锛?
```json
{
  "nrd_macro_previous_snapshot_v1": {
    "ts_ms": 0,
    "macro_score": 0.52,
    "macro_regime": "Headwind",
    "volq_bps": 580.0
  }
}
```

鍙湪褰撳墠 MACRO 鏁版嵁鏈夋晥銆乣macro_score` 瀛樺湪涓?VOLQ bps 鍙彇鏃舵洿鏂般€俙unavailable`銆佸叧閿瓧娈电己澶辨垨寮傚父鏁版嵁涓嶅緱瑕嗙洊涓婁竴浠芥湁鏁堝揩鐓с€?
## EDB 涓庡璁?
- `evaluate_macro_verdict()` 杈撳嚭 `MACRO_ADVERSE/MILD_ADVERSE` 浠ｈ〃鏂瑰悜鑳屾櫙锛涘彧鏈?`macro_shock.block=true` 鏃惰緭鍑?`MACRO_SHOCK_BLOCKING`銆?- `_macro_vote()` 缁х画浣跨敤 `macro_score` 璁＄畻鍘熷鏂瑰悜绁紱鍐插嚮闃绘柇鍙奖鍝嶆渶缁?gate锛屼笉鍒犻櫎鏂瑰悜鑳屾櫙銆?- `evaluate_edb()` 鍦ㄦ渶缁?`lean/support/side_hint/next_action` 澶栵紝淇濈暀 `lean_pre_gate/support_pre_gate/side_hint_pre_gate/next_action_pre_gate`銆?- 瀹¤ JSON 鐨?`decision` 鍖轰繚鐣?pre-gate 瀛楁锛岀敤浜庤В閲娾€滄柟鍚戣儗鏅垚绔嬶紝浣嗗啿鍑婚棬闃绘柇鈥濄€?
## 鍓嶇涓?Materializer

- materializer 鍙€忎紶 producer 鍘熺敓 `macro_shock`锛涘巻鍙插崱缂哄瓧娈垫椂涓嶅緱鍥炲～鎴栭粯璁や负 0銆?- 鍓嶇鍙湪鐜版湁 MACRO 璇佹嵁琛屽鍔狅細
  - `鏂瑰悜鑳屾櫙`
  - `鍐插嚮闂╜
- 鍘嗗彶鍗＄己 `macro_shock` 鏃舵樉绀衡€滃巻鍙插崱鏈彁渚涘啿鍑婚棬瀛楁鈥濓紝涓嶅緱璇樉绀轰负 `CLEAR`銆?- Rank 鍒嗕綅銆丟EX/Gamma銆佸畬鏁磋瘉鎹处鏈€佸洜瀛愬師濮嬫埅闈€乻ource_ref/raw trace 蹇呴』淇濇寔鍙銆?
## 楠屾敹

- `macro_score >= 0.46` 鏈韩涓嶅啀绛変簬纭樆鏂€?- 绋冲畾楂橀€嗛缁х画浜х敓鍋忕┖鑳屾櫙 vote銆?- 纭鍐插嚮璺冭縼蹇呴』杈撳嚭 `MACRO_SHOCK_BLOCKING`銆?- GGR 鐙珛 veto 涓嶈 MACRO 瑕嗙洊銆?- 鏃犳晥 MACRO 鏁版嵁涓嶈鐩?previous snapshot銆?- 鏈嶅姟鍣ㄦ渶鏂扮湡瀹炲崱蹇呴』涓?`identity.strategy_version=1.5.1`锛屽苟甯?producer 鍘熺敓 `factor_cross_section.macro_pressure.macro_shock.state/block`銆?
