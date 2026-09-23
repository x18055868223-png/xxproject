"""Compact neutral-paper supplement: simultaneous protected dual credit spreads."""
import argparse,json
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate,PageBreak,Spacer
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader,PdfWriter
import build_astra_study_pdf as paper

W=paper.WIDTH
NAMES={'put_credit':'仅卖 Put 价差','call_credit':'仅卖 Call 价差','dual_credit':'同时双卖价差'}
def pct(v):return '-' if v is None else f'{v*100:.2f}%'
def ratio(v):return '-' if v is None else f'{v:.2f}'
def pp(v):return f'{v:+.2f}'
def p(t,style='body'):return paper.p(t,style)
class Pages(Canvas):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.saved=[];self.bookmarks=[]
    def showPage(self):self.saved.append(dict(self.__dict__));self._startPage()
    def save(self):
        count=len(self.saved)
        for state in self.saved:
            self.__dict__.update(state);self.setStrokeColor(paper.RULE);self.setLineWidth(.5)
            self.line(paper.MARGIN,34,paper.PAGE_W-paper.MARGIN,34);self.setFillColor(paper.MUTED);self.setFont('CN',7.3)
            self.drawString(paper.MARGIN,22,'Astra / 无脑双卖对照 / 一笔组合，两侧赔付')
            self.setFont('NUM',8);self.drawRightString(paper.PAGE_W-paper.MARGIN,22,f'{self._pageNumber:02d} / {count:02d}');super().showPage()
        super().save()
def page(story,n,title,sub=''):
    if story:story.append(PageBreak())
    paper.section(story,n,title,sub)
def get(d,cohort,side):return next(r for r in d['summary'] if r['cohort']==cohort and r['strategy']==side)
def perf(rows):
    return paper.table(['策略 / 同一批入场','盈利 / 总数','胜率','盈亏比','保证金回报¹','情景 APR²'],
        [[name,f"{m['wins']:.0f}/{m['n']:.0f}",pct(m['win_rate']),ratio(m['payoff_ratio']),pct(m['capital_roi']),pct(m['apr'])] for name,m in rows],
        [125,66,70,56,91,W-408],numeric=(1,2,3,4,5))

def build(study,out):
    d=json.loads((study/'results/report.json').read_text(encoding='utf-8'))
    paper.register_fonts();paper.STYLES=paper.styles();s=[]
    page(s,1,'同时双卖，能否省掉方向判断？','原研究的连续行情、真实合约与交割价；只把两侧合成一笔，旧结果完整保留。')
    s.append(p('<b>普通轮：双卖没有明显信号增量。周末轮：有适配迹象，仍取决于净补偿。</b>','h2'))
    s.append(p('这里的“无脑双卖”是同刻卖出 Put 与 Call 两组信用价差，仍保留两侧保护腿。每侧各按 1 BTC 名义计算，到期一起结算；组合总净盈利才算赢，不用两侧胜率平均，也不把两笔各自盈利作为必要条件。'))
    s.append(p('普通轮：每 30 分钟入场，不读取信号','h2'))
    s.append(perf([(NAMES[a],get(d,'完整时钟',a)) for a in NAMES]))
    s.append(p('主情景：两侧各以目标宽度 2000 美元选保护腿，实际可用宽度保留；每侧净补偿假设为其实际宽度入场折算值的 10%。普通轮仅 8 小时＜剩余期限≤24 小时。2709 个时点来自 86 个交割日，存在大量重叠，并非 2709 次独立试验。','small'))
    s.append(p('双卖多收一份补偿，也多承担另一端侵入。完整时钟组有 1386 笔两侧各自盈利，另有 373 笔由另一侧收入挽回单侧亏损，合计 1759 笔组合盈利；仍有 950 笔总收入不足以覆盖赔付。'))
    s.append(p('为什么不能只看多收的权利金','h2'))
    s.append(p('两侧保证金估算相加后，双卖回报为 +0.60%，位于同期 Put 的 +0.32% 与 Call 的 +0.88% 之间。它减少了单侧选择，但不会机械地把资金回报翻倍。组合的盈亏比有所改善，胜率却低于任一单卖侧。'))
    s.append(p('¹保证金回报＝总净盈亏÷逐笔估算保证金之和，不是期间账户涨幅。²APR按资金占用时长年化，未含闲置资金、账户杠杆或组合抵扣，不是可持续收益预测。所有补偿均是假设净收入，未使用历史双腿报价。','small'))

    page(s,2,'相同交割日与期限下，信号增加了多少？','同一笔总净盈亏口径；每个信号的控制池总权重为 1。')
    s.append(perf([(NAMES[a],get(d,'信号时点',a)) for a in NAMES]))
    s.append(p('114 个信号时点中，固定 Put 的较好表现被同时加入的 Call 明显稀释。双卖盈利 78 笔、亏损 36 笔；这批组合恰好与单卖 Call 盈利笔数相同，但具体盈亏与资金回报并不相同。'))
    rows=[]
    for x in d['matched']:
        rows.append([x['variant'],str(round(x['signal']['n'])),pct(x['signal']['win_rate']),pct(x['control']['win_rate']),pp(x['roi_delta_pp'])])
    s.append(paper.table(['双卖比较','配对信号数','信号胜率','对照胜率','回报差 / 百分点'],rows,[144,66,83,83,W-376],numeric=(1,2,3,4)))
    s.append(p('主比较：信号双卖保证金回报 +0.440%，匹配对照 +0.413%，差距仅 <b>+0.027 个百分点</b>。胜率差 +2.85 个百分点，几乎没有转化为经济增量。按交割日聚类重抽样，回报差的描述性 95% 区间为 −0.84 至 +0.87 个百分点；按周为 −1.04 至 +1.05，均跨零。'))
    s.append(p('匹配复用既定“交割日＋两小时剩余期限箱”，覆盖 114/114 张，实际 429 个控制入场、61 个交割日。主网格平均期限比信号长约 15 分钟；偏移 15 分钟的网格几乎对齐平均期限，回报差仍小且区间跨零。','small'))
    s.append(p('剔除原始事件前后 30 分钟后，2 张失去对照，只比较其余 112 张；回报差转负。该剔除用于事后敏感性检查，不是能提前知道下一个信号的交易规则。','small'))
    s.append(p('能裁定到哪一步','h2'))
    s.append(p('这批数据尚不能证明“等中性回路信号再双卖”有稳定经济优势。它没有证明空间锚无效：固定时钟也可能遇到同样的空间约束，最近虚值选腿也未要求落在有效锚带或墙外。这里只检验当前选腿、固定期限与补偿假设下的使用方式。'))
    s.append(p('不读取信号的完整时钟组双卖回报 +0.60%，信号组 +0.44%；完整组未经日期与期限配对，仅作总体背景，不能取代上面的匹配比较。','small'))

    page(s,3,'时段与周末：双卖更适配哪里？','研究分段沿用北京时间；开盘后与收盘后分别命名。')
    rows=[]
    for x in d['sessions']:
        rows.append([x['session'],str(round(x['signal']['n'])),pct(x['signal']['win_rate']),pct(x['signal']['capital_roi']),pct(x['full_clock']['capital_roi'])])
    s.append(paper.table(['入场时段','信号数','信号双卖胜率','信号回报','全时钟回报'],rows,[155,47,100,102,W-404],numeric=(1,2,3,4)))
    s.append(p('美股收盘后 04:00—08:00 的信号双卖较好，主要对应约 8—12 小时到期；21:30—04:00 是美股交易时段，两者不能混称“美盘后”。这些钟点分组包含休市日，不表示当时美股正在交易。时段与剩余期限耦合，表中全时钟组不是同日期匹配，不能据此宣布独立时段优势。亚盘没有满足期限的样本。','small'))
    s.append(p('周末轮：12 个成熟周六，另 1 个未到期','h2'))
    s.append(perf([(NAMES[a],get(d,'周末轮',a)) for a in NAMES]))
    s.append(p('周六 09:01 重新定价选腿、周一 16:00 到期，约 55 小时。双卖 11/12 盈利：8 轮两侧分别盈利，3 轮由 Put 收入覆盖小额 Call 亏损；7 月 11 日一轮仍因 Put 大额赔付而净亏，估算保证金单轮回报 −17.52%。'))
    s.append(p('双卖回报 +5.31%，处于固定 Put 与固定 Call 之间；它在这批周末兼顾了较高胜率与正回报，但样本仅 12 轮。周末入场本来就是每周固定一次，成绩不能归给来源卡的择时。'))
    s.append(p('中性标签本身不保证双卖适配','h2'))
    s.append(p('48 张中性信号双卖胜率 68.75%，保证金回报却为 −0.24%；66 张有方向信号双卖为 68.18%、+0.95%，按原方向只卖一侧为 77.27%、+1.47%。既不能把中性直接翻译成双卖，也不能只以胜率判断收益。'))

    page(s,4,'补偿才是这组成绩的敏感开关','目标宽度不是实际风险距离；三档补偿不是历史成交价。')
    rows=[]
    for x in d['sensitivity']:
        rows.append([str(x['target_width']),pct(x['credit_fraction']),pct(x['full_clock']['win_rate']),pct(x['full_clock']['capital_roi']),pct(x['signal']['capital_roi']),pct(x['weekend']['capital_roi'])])
    s.append(paper.table(['每侧目标宽 USD','每侧补偿','全时钟胜率','全时钟回报','信号回报','周末回报'],rows,[83,70,88,87,87,W-415],numeric=(0,1,2,3,4,5)))
    s.append(p('主宽度 2000 美元时，补偿从 10% 减到 5%，普通时钟双卖回报由 +0.60% 变为 −4.66%；周末由 +5.31% 降至 +0.06%。当前正回报很依赖是否真能收到足够净补偿。'))
    s.append(p('保持相同相对补偿比例时，全时钟双卖的样本总体盈亏平衡补偿约为每侧宽度的 <b>9.43%</b>；信号组约 <b>9.58%</b>，周末约 <b>4.95%</b>。这是事后使样本总 BTC 盈亏为零的情景值，不是卖出门槛，也不是任何一笔交易的公平报价。'))
    s.append(p('宽度敏感性怎样解释','h2'))
    s.append(p('保护腿放远时，本研究同时按宽度比例增加了假设收入，因此 2500 宽表现变好不能直接解释为“宽一些更好”。真实更远保护腿的净收入、风险和保证金必须共同核对；不以这张表替代实际报价。'))
    s.append(p('保证金如何公平比较','h2'))
    s.append(p('每侧估算保证金为 λ×实际宽度÷入场价，λ≈0.951659；双卖取两侧之和。合计盈亏与该分母同时按比例缩放，单笔回报率不变。若真实组合占用仅为相加估算的一半，同一盈亏的回报与 APR 会翻倍，但胜率和净盈亏不变；本轮没有证据认定交易所实际会减半。'))
    s.append(p('同到期的严格虚值两侧不可能在结算时同时产生内在赔付；盘中仍可先后触及两边。本研究持有到期，不模拟盘中保证金追加、止损或强平。币本位赔付除以交割价，不能把美元风险上限当作固定 BTC 上限。','small'))

    page(s,5,'周末逐笔与下一步裁定','逐笔为两侧各 1 BTC 名义；收益比例使用两侧相加的估算保证金。')
    rows=[]
    for r in d['weekend_main']:
        rows.append([r['entry_beijing'][:10],'-' if r['put_net_pnl_btc'] is None else f"{r['put_net_pnl_btc']:+.6f}",
            '-' if r['call_net_pnl_btc'] is None else f"{r['call_net_pnl_btc']:+.6f}",
            '未到期' if not r['is_matured'] else f"{r['net_pnl_btc']:+.6f}",pct(r['holding_return_on_margin'])])
    s.append(paper.table(['周六入场','Put 净盈亏 BTC','Call 净盈亏 BTC','合计 BTC','组合回报'],rows,[91,111,111,102,W-415],numeric=(1,2,3,4)))
    s.append(p('当前最有用的结论','h2'))
    s.append(p('普通轮不宜因为“双收权利金”就改成固定双卖：当前信号优势接近零，选错另一侧会稀释原有较好一侧。周末双卖可以保留为待验证候选，但先记录少量真实四腿净收入及新增保证金，判断是否覆盖这里的赔付，而非先增加评级或工程。'))
    s.append(p('可复算与资料边界','h2'))
    s.append(p('49,905 条组合情景记录：控制 48,762、普通信号 1,026、周末 117；包含两套网格、三种宽度、三档补偿，不能把这些重复情景当新增独立样本。普通轮少于或等于 8 小时的 62 张仍排除。未到期周末保留，不拉取未来数据补完。'))
    s.append(p('数据包保留组合逐笔 CSV、匹配权重、冻结输入哈希、脚本和验收记录。前序原始行情、合约、交割与 LLM 资料不改写；本轮不增加市场或模型调用，不更改 FMZ 与生产系统。','small'))
    s.append(p('来源：<link href="https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options" color="#34495E">[1] Deribit Inverse Options：币本位到期赔付</link>；<link href="https://insights.deribit.com/education/multi-leg-options-positions-part-3-butterflies-and-condors/" color="#34495E">[2] Deribit 多腿期权：保护型双卖结构</link>。价格与合约使用既有冻结研究资料，不是页面报价。','small'))
    out.parent.mkdir(parents=True,exist_ok=True)
    scratch=out.with_suffix('.draft.pdf')
    doc=SimpleDocTemplate(str(scratch),pagesize=paper.A4,rightMargin=paper.MARGIN,leftMargin=paper.MARGIN,topMargin=49,bottomMargin=48)
    doc.build(s,onFirstPage=paper.header,onLaterPages=paper.header,canvasmaker=Pages)
    reader=PdfReader(scratch);writer=PdfWriter();writer.append(reader)
    for name in ('study_config.json','results/summary.csv','results/dual_main.csv','results/acceptance.json'):
        writer.add_attachment(Path(name).name,(study/name).read_bytes())
    writer.add_metadata({'/Title':'Astra 无脑双卖对照研究','/Author':'Astra','/Subject':'冻结样本与时钟控制；保护型双侧信用价差'})
    with out.open('wb') as f:writer.write(f)
    scratch.unlink();print(f'{out} | {len(reader.pages)} pages')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--study',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();build(a.study,a.output)
