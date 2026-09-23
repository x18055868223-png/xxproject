"""Neutral paper control-study supplement from frozen numerical results."""
import argparse,json
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate,PageBreak,Spacer,TableStyle
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader,PdfWriter
import build_astra_study_pdf as paper

W=paper.WIDTH
LABEL={'put_credit':'Put','call_credit':'Call'}
def pct(v):return '-' if v is None else f'{v*100:.1f}%'
def pp(v):return '-' if v is None else f'{v:+.2f}'
def num(v):return '-' if v is None else f'{v:.2f}'
def p(t,style='body'):return paper.p(t,style)
def interval(v):return '-' if v is None else f'{v[0]:+.2f} 至 {v[1]:+.2f}'

class Pages(Canvas):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.saved=[];self.bookmarks=[]
    def showPage(self):self.saved.append(dict(self.__dict__));self._startPage()
    def save(self):
        count=len(self.saved)
        for state in self.saved:
            self.__dict__.update(state);self.setStrokeColor(paper.RULE);self.setLineWidth(.5)
            self.line(paper.MARGIN,34,paper.PAGE_W-paper.MARGIN,34)
            self.setFillColor(paper.MUTED);self.setFont('CN',7.4)
            self.drawString(paper.MARGIN,22,'Astra / 无信号对照 / 结构基准与信号增量')
            self.setFont('NUM',8);self.drawRightString(paper.PAGE_W-paper.MARGIN,22,f'{self._pageNumber:02d} / {count:02d}')
            super().showPage()
        super().save()

def page(story,n,title,sub=''):
    if story:story.append(PageBreak())
    paper.section(story,n,title,sub)
def table(head,rows,widths,numeric=()):
    t=paper.table(head,rows,widths,numeric=numeric)
    t.setStyle(TableStyle([('LEFTPADDING',(1,0),(-1,-1),5),('RIGHTPADDING',(1,0),(-1,-1),5)]))
    return t
def mrow(label,m):return [label,str(round(m['n'])),pct(m['win_rate']),pct(m['intrusion_rate']),num(m['payoff_ratio']),pct(m['capital_roi']),pct(m['total_payout_to_credit'])]
def perf(story,groups):
    story.append(table(['组别','样本量¹','胜率','侵入率','盈亏比','保证金回报²','赔付/补偿'],[mrow(a,m) for a,m in groups],[125,46,55,55,52,85,W-418],(1,2,3,4,5,6)))
    story.append(Spacer(1,8))
def match(d,side,group='全部信号',variant='主时钟'):
    return next(r for r in d['matched'] if r['variant']==variant and r['group']==group and r['side']==side)
def raw(d,side,group):return next(r for r in d['raw'] if r['side']==side and r['group']==group)

def build(study,output,scratch):
    d=json.loads((study/'results/report.json').read_text(encoding='utf-8'))
    paper.register_fonts();paper.STYLES=paper.styles();story=[]
    page(story,1,'高胜率来自哪里','同一历史行情，固定重复信用价差，对照中性回路信号。')
    story.append(p('高胜率本身，<br/>还不能证明信号有优势。','title'))
    story.append(p('不读取中性回路，单纯按固定时钟卖最近虚值垂直价差，本批主情景也出现约74%至77%的盈利比例。中性回路在Put侧有额外改善迹象，Call侧没有体现相同优势。'))
    rows=[]
    for side in ('put_credit','call_credit'):
        r=match(d,side);b=raw(d,side,'主时钟')
        rows.append([LABEL[side],pct(b['win_rate']),pct(r['control']['win_rate']),pct(r['signal']['win_rate']),pp(r['win_delta_pp'])+' pp'])
    story.append(table(['侧别','全时钟基准','同日/期限对照','信号样本','匹配后差值'],rows,[50,109,125,105,W-389],(1,2,3,4)))
    story.append(Spacer(1,12))
    story.append(p('主情景：2000美元目标保护宽度，假设净补偿为实际宽度入场折算价值的10%；侵入=到期净亏损。普通轮114张，固定时钟2709个入场点；2709不是2709次独立实验。','small'))
    story.append(p('本轮能够支持的判断','h2'))
    story.append(p('期权结构、这段市场路径以及补偿假设，已经提供了较高基础胜率。信号可能改善某些侧别或选择某些日期，但尚不能认定它带来普遍、稳定、可成交的盈利增量。'))
    story.append(p('Put匹配后的保证金情景回报提高约0.84个百分点，区间仍跨零；Call约低0.73个百分点。周末固定Put/Call的原成绩，本来就不需要信号来决定入场。'))

    page(story,2,'对照怎样建立','先冻结规则，再计算对照结果；没有新增模型或行情请求。')
    rows=[
      ['相同范围','2026年6月19日22:31至9月12日17:11，北京时间；沿用114张普通信号的模拟入场范围。'],
      ['期限','8小时＜剩余期限≤24小时，最近一次北京时间16:00到期。恰好8小时排除，24小时纳入。'],
      ['主基准','每逢:00/:30固定入场，不看Anchor、方向、评级或有没有信号；2709个点、86个交割日，Put和Call分别统计。'],
      ['选腿与赔付','各自入场价格重新选择最近严格虚值的真实短腿及保护腿；只用入场前已创建合约。到期按Deribit官方交割价计算完整币本位两腿赔付。'],
      ['公平匹配','同交割日期×2小时剩余期限箱×侧别×宽度×补偿。每张信号对其合格对照均分权重，总权重1；控制日期及期限分布。'],
      ['匹配覆盖','114/114张都有对照；用了429个不重复时钟入场点，来自61个交割日。对照的114是加权规模，不是另一组114个独立事件。'],
      ['稳健性','固定网格整体偏移15分钟；另剔除所有176事件确认时点前后30分钟。后者是事后环境敏感性，不是可实盘执行的未来事件过滤。'],
      ['收益与保证金','净补偿5%/10%/20%、保护宽1500/2000/2500。保证金仍按单例系数λ≈0.951659估算，不是历史实际保证金。'],
    ]
    story.append(table(['项目','固定口径'],rows,[75,W-75]))
    story.append(p('来源分钟线连续、交割价和已创建合约均可覆盖本次候选。两套时钟合计5418个点，97,524行情景，无缺价或缺腿排除。原研究及生产系统保持原样。','small'))
    story.append(p('入场使用Binance BTCUSDT现货分钟开盘，结算使用Deribit美元交割指数；沿用前研究的价格代理，未逐分钟校正跨市场基差。','small'))
    story.append(p('“不依赖信号”不等于“没有空间锚”。全时钟基准中可以偶然出现信号，也可能存在未触发事件的空间结构；本轮检验的是事件选样的附加表现，不能单独拆出Anchor这个因素的因果贡献。','small'))

    page(story,3,'策略基准与信号出现时点','相同市场范围的总体比较，与同日/期限匹配比较，都需要保留。')
    groups=[]
    for side in ('put_credit','call_credit'):
        r=match(d,side)
        groups.extend([(LABEL[side]+' 全时钟',raw(d,side,'主时钟')),(LABEL[side]+' 匹配对照',r['control']),(LABEL[side]+' 信号时点',r['signal'])])
    perf(story,groups)
    story.append(p('¹ 全时钟是实际入场数；信号是事件数；匹配对照是总权重，实际429个不同入场点。² 保证金回报＝合计净盈亏／合计估算保证金；不是账户收益。盈亏比为平均盈利／平均亏损绝对值。','small'))
    story.append(p('日期与期限分布解释了部分差距','h2'))
    story.append(p('Put信号胜率比全时钟高11.84个百分点；换成同日、相近期限对照后，差距为3.73个百分点。Call则从比全时钟低8.62个百分点，变成低4.61个百分点。'))
    story.append(p('这不意味着被匹配消除的差距“没有价值”：信号选择了哪些日期和市场阶段，本身也可能属于择时作用。这里把日期/期限选择与更局部的入场时点差异分开，避免把所有差距都解释成精确信号。'))
    story.append(p('固定Put和Call都能有较高胜率，但主情景保证金回报仅约0.32%和0.88%。信号Put为3.69%，Call为−2.74%；盈利次数不能替代赔付大小和净补偿。'))
    story.append(p('赔付/补偿超过100%表示总赔付已吃掉全部情景收入。净补偿是假设，实际盘口可能改变上述净收益方向。','small'))

    page(story,4,'小幅增量有多稳定','重复入场共享行情；不按数千行独立样本计算置信程度。')
    rows=[]
    for side in ('put_credit','call_credit'):
        r=match(d,side)
        rows.append([LABEL[side],pp(r['win_delta_pp']),interval(r['by_delivery_day']['win_delta_pp_interval']),
                     pp(r['roi_delta_pp']),interval(r['by_delivery_day']['roi_delta_pp_interval'])])
    story.append(table(['侧别','胜率差 pp','按交割日重采样95%区间','回报差 pp','回报差95%区间'],rows,[42,69,158,72,W-341],(1,2,3,4)))
    story.append(p('61个交割日作为重采样单位，固定种子、2000次；每次信号和匹配对照一起取出。区间是本批聚类重采样的描述，不是因果识别或未来盈利保证。','small'))
    rows=[]
    for variant,label in [('主时钟','主时钟'),('偏移15分钟','网格偏移15分'),('剔除事件前后30分钟','剔除事件附近')]:
        for side in ('put_credit','call_credit'):
            r=match(d,side,variant=variant)
            rows.append([label+' '+LABEL[side],str(round(r['signal']['n'])),pp(r['win_delta_pp']),pp(r['roi_delta_pp']),f"{r['dte_delta_hours']*60:+.1f}分"])
    story.append(table(['对照方式','匹配卡数','胜率差 pp','回报差 pp','信号-对照期限'],rows,[154,63,86,86,W-389],(1,2,3,4)))
    story.append(p('主网格对照平均比信号多约15分钟；偏移15分钟后，平均期限几乎一致。Put回报增量随网格从0.84降至0.39个百分点，Call增量从−0.73变为+0.07个百分点；经济增量不够稳定。'))
    story.append(p('事件附近剔除后剩2486个主时钟点；2张信号同箱无对照，112张参与该敏感性，未匹配卡已列明。它使用了未来事件的位置，仅供回顾诊断。'))
    r=match(d,'put_credit');c=match(d,'call_credit')
    story.append(p('进一步按13个交割周聚类，Put回报差95%区间为'+interval(r['by_delivery_week']['roi_delta_pp_interval'])+'个百分点；Call为'+interval(c['by_delivery_week']['roi_delta_pp_interval'])+'个百分点，仍均跨零。','small'))

    page(story,5,'方向与开盘后偏好','“美股交易时段”与“美股收盘后”明确分开，不再混称美盘后。')
    perf(story,[(r['group'],r) for r in d['direction_diagnostic']])
    story.append(p('以上仅66张有明确原方向的卡。两侧等权仅是统计诊断，不是建议同时双卖；中性48张未被事后挑选有利侧。原方向胜率77.3%，低于等权78.0%和同卡固定Put87.9%，但保证金回报略高于等权，不能只按胜率给方向结论。','small'))
    rows=[]
    for label in ['美股交易时段 21:30—04:00','美股收盘后 04:00—08:00']:
        for side in ('put_credit','call_credit'):
            r=match(d,side,label)
            rows.append([label+' '+LABEL[side],str(round(r['signal']['n'])),pct(r['signal']['win_rate']),pct(r['control']['win_rate']),pp(r['win_delta_pp']),pp(r['roi_delta_pp'])])
    story.append(table(['时段/侧别','卡数','信号胜率','对照胜率','差 pp','回报差 pp'],rows,[177,33,75,75,63,W-423],(1,2,3,4,5)))
    story.append(p('你说的21:30开盘后，主要对应55张“美股交易时段”。这一组Put有改善迹象，Call没有。04:00—08:00的高胜率，在匹配对照里也相当高；不能把它全部归于信号。'))
    story.append(p('时段按信号入场分组；对照仍用同日2小时期限箱，21:30附近的箱可能跨开盘分界，因此这些小组仅作粗粒度诊断，不识别开盘的独立效果。纽约休市日也未伪装成真实美股开盘；固定钟点只是本研究时间标签。','small'))

    page(story,6,'补偿和宽度会改变怎样的答案','同一报价假设用于信号和对照；差距不是来自给信号更高的假设收入。')
    rows=[]
    for width in (1500,2000,2500):
        for q in (.05,.1,.2):
            a=next(r for r in d['sensitivity'] if r['target_width']==width and r['credit_fraction']==q and r['side']=='put_credit')
            b=next(r for r in d['sensitivity'] if r['target_width']==width and r['credit_fraction']==q and r['side']=='call_credit')
            rows.append([str(width),pct(q),pp(a['win_delta_pp']),pp(b['win_delta_pp']),pp(a['roi_delta_pp']),pp(b['roi_delta_pp'])])
    story.append(table(['目标宽USD','净补偿比例','Put胜率差 pp','Call胜率差 pp','Put回报差 pp','Call回报差 pp'],rows,[70,70,93,93,93,W-419],(0,1,2,3,4,5)))
    story.append(p('每行都是“信号减匹配对照”，不是实际收益。Put胜率差多数为正；Call在较高补偿下胜率差可以接近零或变正，但保证金回报差仍负。补偿改变了盈亏平衡边界，胜率和经济增量不能互相替代。'))
    story.append(p('同一宽度下，回报差不随三档补偿变化，是本模型的数学性质：两组每笔收入/估算保证金均为q/λ，差值抵消。不能把三档相同差距当作三份独立验证。'))
    story.append(p('一个需要真实报价回答的问题','h2'))
    story.append(p('净补偿比例若相差1个百分点，对应保证金情景回报就相差约1.05个百分点，足以超过Put主比较约0.84个百分点的增量。因此，当前数据尚不足以确定这点统计改善能否覆盖真实报价、成交和费用差异。'))
    story.append(p('币本位结算：两腿美元内在价值之差，再除以到期交割价。补偿按入场价格折成BTC，两者不可混用。Deribit官方机制见末页[1]。','small'))

    page(story,7,'周末轮已经自带固定时点基准','每周六09:01入场这一动作，在上一轮并没有由信号决定。')
    perf(story,[(r['group']+' '+LABEL.get(r['side'],''),r) for r in d['weekend'][:2]])
    story.append(p('每周六09:01都入场、周一16:00到期；去掉来源卡后，固定Put/Call的价格、合约、补偿、赔付完全不变。上表与上一报告12个成熟周末成绩一致，最后1个未到期轮仍不计成绩。'))
    story.append(p('因此，周末Put 91.7%、Call 75.0%的总体胜率，本身不能作为中性回路贡献的证据。它们体现的是这些周末、固定期权结构及补偿假设的组合表现。'))
    perf(story,[(r['group'],r) for r in d['weekend'][2:]])
    story.append(p('来源卡明确偏多/偏空的周末只有5轮。按其方向3／5盈利，同轮固定Put5／5、固定Call2／5；方向侧保证金回报介于两者之间。这是小样本选侧比较，不能证明方向稳定增益，也不能因5／5就确立固定Put规则。'))
    story.append(p('中性来源的7轮继续保留两个侧别，没有挑选赚钱侧。原先观察到的低周末振幅仍是波动背景证据，不是信号择时或真实净权利金优势。','small'))

    page(story,8,'再看B级：选择了好环境，还是额外赚得更多','直接复用已经封存的历史资料下当前标准离线评级，没有新评审。')
    rows=[]
    for side in ('put_credit','call_credit'):
        r=match(d,side,'既有离线B级')
        rows.append([LABEL[side]+' B',str(round(r['signal']['n'])),pct(r['signal']['win_rate']),pct(r['control']['win_rate']),pct(r['signal']['capital_roi']),pct(r['control']['capital_roi'])])
    story.append(table(['等级侧别','卡数','B信号胜率','匹配对照胜率','B信号回报','对照回报'],rows,[73,40,92,112,96,W-413],(1,2,3,4,5)))
    story.append(p('Put B在上一轮看起来比未选组好；但与相同日期和期限的时钟对照相比，保证金回报几乎相同，约4.42%对4.37%。这说明先前的改善可能主要反映选中了较有利的环境，尚未证明精确入场额外创造利润。'))
    story.append(p('Call B胜率低于匹配对照，保证金回报略好，但两组仍为负。更高胜率、更高等级和更高经济收益不是同一件事。'))
    story.append(p('当前不能据此调整等级门槛','h2'))
    story.append(p('本批仍没有A/S；历史近端证据稀疏，B分布与版本、日期、资料完整度相关。这里是附加描述分组，不能把54个Put B或45个Call B当作独立验证评级因果效果的试验。'))
    story.append(p('27个未评级侧保留在前一报告和原收益表中。本轮“全部信号”比较没有按评级删卡；B分析只调用现有封存字母，未恢复被校验拦截的结果。','small'))

    page(story,9,'当前最可靠的结论与下一步','完成一个最低成本的无信号对照，收窄后续研究问题。')
    rows=[['基础胜率','在相同行情及补偿假设下，不看信号也能取得约74%至77%胜率。高胜率本身不构成模型优势。'],
      ['事件附加表现','Put在总体与匹配对照中有改善迹象；匹配后经济增量较小且区间跨零。Call没有一致的正向增量。'],
      ['方向与评级','原方向没有稳定超过固定侧或等权诊断；B的优势与环境选择交织。现有证据不支持立即把方向或B作为硬过滤规则。'],
      ['周末','固定周六入场成绩不依赖信号择时；来源卡选侧的独立贡献仍未证明。'],
      ['可归因程度','本轮是同市场观察性对照，无法将市场漂移、空间锚、触发逻辑和策略结构分解成可信的贡献百分比。没有随机实验，不等于已经证明或否定全部信号价值。']]
    story.append(table(['问题','本轮判断'],rows,[77,W-77]))
    story.append(p('下一项最小验证','h2'))
    story.append(p('将Put侧小幅改善保留为待验证假设，在后续固定批次沿用同一时钟对照；先记录少量实际两腿净补偿与新增保证金，判断约0.4至0.8个百分点的估算回报差有没有可成交意义。优先核对偏空信号至Call价差的转译，不因这批成绩反向调权或细分评级。'))
    story.append(p('本研究固定使用最近虚值腿，它可能仍在锚带或墙内。结论只对应这套使用方式，不覆盖所有结构选腿和退出方法；本轮没有止损、移腿或实盘盘口。','small'))
    story.append(p('资料与复现','h2'))
    story.append(p('<a href="https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options">[1] Deribit：币本位期权与到期交割。</a>','small'))
    story.append(p('<a href="https://github.com/binance/binance-public-data">[2] Binance：公开分钟行情资料。</a> 本轮复用已冻结文件，未新增数据或LLM请求。','small'))
    story.append(p('附件含主情景时钟逐笔、信号逐笔、匹配权重、对照汇总和固定配置。完整三宽度/三补偿结果与代码另随结果包交付。原两份PDF、FMZ与生产系统均未改写。','small'))

    scratch.mkdir(parents=True,exist_ok=True);output.parent.mkdir(parents=True,exist_ok=True)
    layout=scratch/'layout.pdf'
    doc=SimpleDocTemplate(str(layout),pagesize=A4,leftMargin=paper.MARGIN,rightMargin=paper.MARGIN,
        topMargin=47,bottomMargin=47,title='Astra 无信号对照：高胜率来源与信号增量',author='Astra')
    doc.build(story,onFirstPage=paper.header,onLaterPages=paper.header,canvasmaker=Pages)
    reader=PdfReader(layout);writer=PdfWriter();writer.clone_document_from_reader(reader)
    for title,n in doc.canv.bookmarks:writer.add_outline_item(title,n)
    for display,relative in [('时钟策略主情景逐笔.csv','results/control_main.csv'),('信号主情景逐笔.csv','results/signal_main.csv'),
        ('匹配权重逐笔.csv','results/matched_links.csv'),('匹配结果汇总.csv','results/matched_summary.csv'),('冻结研究口径.json','study_config.json')]:
        writer.add_attachment(display,(study/relative).read_bytes())
    writer.add_metadata({'/Title':'Astra 无信号对照：高胜率来源与信号增量','/Author':'Astra'})
    with output.open('wb') as f:writer.write(f)
    print(json.dumps({'pdf':str(output),'pages':len(reader.pages)},ensure_ascii=False))

if __name__=='__main__':
    pser=argparse.ArgumentParser();pser.add_argument('--study',type=Path,required=True);pser.add_argument('--output',type=Path,required=True)
    pser.add_argument('--scratch',type=Path,required=True);args=pser.parse_args();build(args.study,args.output,args.scratch)
