# 牌手完整输入：使用与验收

本轮依据用户《牌手完整输入：实现说明》实现。当前入口是 tools/astra_player_input_v1.py；输出为一份含规则的Markdown正文，不新增Schema或评分协议。历史0.5的TMV准入要求不再适用于当前牌手正文。

直接读 ai_input.md。验收用2026-11-11 14UTC真实候选：Put73,000/71,000、Call74,500/76,500；不使用外移演示腿。两侧入区前分别只有73,500和74,000，后者保留本期Pin。原生本期IV尺度是1,429.5 USD，不照搬用户样例“未提供”。可用归档指数路径严格截至各侧读取时刻，已发生24h极值与近5分钟指数变化进入正文，未使用交割后数据或评价。

## 从已有引擎档案生成

在当前工作树执行（输出文件必须不存在）：

```powershell
Set-Location 'C:/Users/Xu/Documents/中性回路整合工程/.worktrees/claude-astra'
python tools/astra_player_input_v1.py --archive docs/rulebook/examples/player_input_path/source --expiry-day 2026-11-12 --round r2 --out hand.md
```

实际引擎档案根为 /var/lib/xxproject/research_archive；替换archive、到期日和轮次即可使用同一入口。它读取原生levels并按case_id找到真实cases，绝不重新选腿；存在原生paths时自动读取并截断。也支持 --put-case、--call-case、--levels 三个显式路径。Python函数build_input(put_case, call_case, put_card, call_card)返回同样正文。

已知事件取原案卷L7.events；额外明确外源干扰可用 --events 传已有contract_events格式（from_ms为已知时点，events[*].at_ms为发生时间），不建新事件协议。事件在读取至到期之间则不创建正文，退出码2，区别于牌手Reject。没有标记不因未知宏观而不发牌；不声称已查遍所有事件源。

尺度只消费同一期、有效本期ATM IV配套expected_move_usd，缺失写未提供，不用已实现波动或其它指标替代。KPF只消费原生reference_pool中可用、可共位且落入入区前的接受区；同站合并，独立区保留独立边界，不借邻站路径。已有代码将价格切换边侧记录为穿越；恰好触及但未穿过会如实写“到达过该位”，不写成没碰到。单次穿过写一次，其余按数次/反复定性，不在正文报次数。

## 接入边界

现在可以从既有引擎档案一条命令导出完整输入，直接发给AI。没有修改线上旧牌手的评分协议、交易逻辑或生产任务，也未部署。后续调用方应发送生成的正文，不把旧评分输入叠加进去。持仓时仍须由调用方锁定原责任并附入场条件/破坏标准，调用相同生成函数读新截面；正文不自动代替入场记录或执行退出。

source/仅存验收所需原案卷、两张原位置卡与截去读取后点位的指数路径；案卷内旧意见不进入正文。两侧冻结相差约8秒，保留本侧时钟，没有强改成同刻。
