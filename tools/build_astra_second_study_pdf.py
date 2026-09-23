"""Neutral-paper sequel built only from the sealed second-study report data."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, PageBreak, Spacer, TableStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.graphics.shapes import Drawing, Rect, String, Line
from pypdf import PdfReader, PdfWriter
import build_astra_study_pdf as paper

BJT=timezone(timedelta(hours=8))
W=paper.WIDTH
LABELS={'BULLISH':'偏多','BEARISH':'偏空','NEUTRAL':'中性','put_credit':'Put','call_credit':'Call'}

class PageCanvas(Canvas):
    def __init__(self,*a,**k):
        super().__init__(*a,**k); self.saved=[]; self.bookmarks=[]
    def showPage(self):
        self.saved.append(dict(self.__dict__)); self._startPage()
    def save(self):
        count=len(self.saved)
        for state in self.saved:
            self.__dict__.update(state)
            self.setStrokeColor(paper.RULE); self.setLineWidth(.5)
            self.line(paper.MARGIN,34,paper.PAGE_W-paper.MARGIN,34)
            self.setFillColor(paper.MUTED);self.setFont('CN',7.4)
            self.drawString(paper.MARGIN,22,'Astra  /  二阶研究  /  时段、周末与证据评级')
            self.setFont('NUM',8)
            self.drawRightString(paper.PAGE_W-paper.MARGIN,22,f'{self._pageNumber:02d} / {count:02d}')
            super().showPage()
        super().save()

def p(text,style='body'): return paper.p(text,style)
def pct(x):return '-' if x is None else f'{x*100:.1f}%'
def fnum(x,dec=2):return '-' if x is None else f'{x:.{dec}f}'
def mnum(x):return '-' if x is None else f'{x*1000:+.3f}'
def date(ms,fmt='%m/%d %H:%M'):
    return datetime.fromtimestamp(float(ms)/1000,BJT).strftime(fmt)
def newpage(story,n,title,subtitle=''):
    if story:story.append(PageBreak())
    paper.section(story,n,title,subtitle)

def result_table(headers,rows,widths,numeric=()):
    return paper.table(headers,rows,widths,numeric=numeric)

def metric_rows(groups):
    return [[label,str(m['n']),pct(m['win_rate']),pct(m['intrusion_rate']),fnum(m['payoff_ratio']),pct(m['capital_roi']),pct(m['apr']),
             fnum(m['avg_loss_btc']*1000 if m['avg_loss_btc'] is not None else None,3),pct(m['tail_to_credit'])] for label,m in groups]

def performance(story,groups,footnote=False):
    tab=result_table(['组别','侧数','胜率','侵入率','盈亏比','持有回报','情景APR','平均亏损<br/>mBTC','尾部赔付<br/>/总补偿'],metric_rows(groups),[88,25,44,44,40,51,65,65,W-422],numeric=(1,2,3,4,5,6,7,8))
    tab.setStyle(TableStyle([('LEFTPADDING',(1,0),(-1,-1),3),('RIGHTPADDING',(1,0),(-1,-1),3)]))
    story.append(tab)
    if footnote:story.append(p('主情景：目标宽2000美元，净补偿10%。分组持有回报＝合计净盈亏／合计估算保证金；APR另按资金占用时长年化。无盈利或无亏损组的盈亏比留空。','small'))
    story.append(Spacer(1,9))

def win_chart(groups):
    """Compact side-by-side win rates; bar lengths never imply sample precision."""
    d=Drawing(W, len(groups)*31+28)
    x=107; scale=(W-x-58)/100
    for i,(label,put,call) in enumerate(groups):
        y=(len(groups)-i-1)*31+23
        d.add(String(0,y+5,label,fontName='CN',fontSize=9,fillColor=paper.INK))
        for j,m in enumerate((put,call)):
            yy=y+(7 if j==0 else -4)
            if m['n']:
                d.add(Rect(x,yy,m['win_rate']*100*scale,7,fillColor=colors.HexColor('#454A4E' if j==0 else '#ADB2B4'),strokeColor=None))
                d.add(String(x+m['win_rate']*100*scale+5,yy,pct(m['win_rate']),fontName='NUM',fontSize=7.5,fillColor=paper.MUTED))
    d.add(String(x,2,'深灰 Put   浅灰 Call；仅展示到期情景胜率',fontName='CN',fontSize=7.5,fillColor=paper.MUTED))
    return d

def export(story,data,study,output,scratch):
    scratch.mkdir(parents=True,exist_ok=True);output.parent.mkdir(parents=True,exist_ok=True)
    raw=scratch/'second-study-layout.pdf'
    doc=SimpleDocTemplate(str(raw),pagesize=A4,leftMargin=paper.MARGIN,rightMargin=paper.MARGIN,
        topMargin=47,bottomMargin=47,title='Astra 二阶轻量研究：时段、周末与评级效用',author='Astra',pageCompression=1)
    doc.build(story,onFirstPage=paper.header,onLaterPages=paper.header,canvasmaker=PageCanvas)
    reader=PdfReader(raw);writer=PdfWriter();writer.clone_document_from_reader(reader)
    for title,page in doc.canv.bookmarks:writer.add_outline_item(title,page)
    for display,relative in [('普通轮_主情景逐笔.csv','report/普通轮_主情景逐笔.csv'),
                             ('周末轮_主情景逐笔.csv','report/周末轮_主情景逐笔.csv'),
                             ('普通轮_全部补偿与宽度.csv','report/ordinary_joined.csv'),
                             ('周末轮_全部补偿与宽度.csv','report/weekend_joined.csv'),
                             ('研究固定配置.json','study_config.json')]:
        path=study/relative
        writer.add_attachment(display,path.read_bytes())
    writer.add_metadata({'/Title':'Astra 二阶轻量研究：时段、周末与评级效用','/Author':'Astra',
        '/Subject':'114张普通轮候选、13次周末观察及当前标准离线评级；净补偿和保证金均为研究情景'})
    with output.open('wb') as f:writer.write(f)
    print(json.dumps({'pdf':str(output),'pages':len(reader.pages)},ensure_ascii=False))

def build(study,output,scratch,data=None):
    paper.register_fonts();paper.STYLES=paper.styles()
    if data is None:data=json.loads((study/'report/report_data.json').read_text(encoding='utf-8'))
    from astra_second_report import metrics,subset
    main=data['ordinary_main_rows'];weekend=data['weekend_main_rows']
    group=lambda cohort,d,s:next(x for x in data[cohort+'_groups'] if x['direction']==d and x['side']==s)
    gf=lambda side,label:next(x for x in data['grade_filters'] if x['side']==side and x['filter']==label)
    groups=lambda cohort:[(LABELS[d]+' '+LABELS[s]+('（原方向）' if (d,s) in [('BULLISH','put_credit'),('BEARISH','call_credit')] else ''),group(cohort,d,s)) for d in ('BULLISH','BEARISH','NEUTRAL') for s in ('put_credit','call_credit')]
    story=[]
    newpage(story,1,'时段、周末与评级效用','176张接管事件的二阶轻量研究 · 114张普通轮候选 · 12轮成熟周末')
    story.append(p('优先观察什么，<br/>需要避免相信什么。','title'))
    story.append(p('本篇保留真实信号与真实到期价格，以假设净补偿检验筛选方式。收益数字属于情景研究，完整逐笔结果与离线评级另附。'))
    post=next(x for x in data['sessions'] if x['session']=='美盘后')
    story.append(result_table(['问题','本批结果支持的回答'],[
        ['美盘后值得优先观察吗？',f"有值得继续验证的迹象。21张卡中，Put胜率{pct(post['put']['win_rate'])}，Call为{pct(post['call']['win_rate'])}。它同时对应平均{post['put']['dte_mean']:.1f}小时剩余期限，不能单独归功于时区。"],
        ['周末低波动有用吗？','12个成熟周末的振幅都低于其后同长工作日窗口。不过，中性Put的6/7胜率仍没覆盖一次较大亏损，低波动不能代替净补偿。'],
        ['原方向值得直接跟随吗？','偏多Put仍有较好表现；偏空Call没有对称优势。美盘后13张有方向卡的相反侧全部盈利，说明“时段合适”和“方向正确”需要分别检验。'],
        ['较高证据等级更好吗？','B组有局部改善：Call胜率73.3%，未选65.2%，但B组仍略有净亏；Put提升较小。本批没有A/S，尚不能确认“等级越高越好”。'],
    ],[122,W-122]))
    story.append(p('阅读数字的前提','h2'))
    story.append(p('主结果使用目标宽2000美元、净补偿10%情景；实际可用宽度逐笔保留。侵入指到期净亏损，途中触及不判负。普通轮与约55小时周末轮分别统计，不视为一个账户的同时持仓。'))

    newpage(story,2,'本轮口径与样本','固定分组后进行计算，先封存评级，再连接到期结果。')
    story.append(result_table(['项目','固定口径'],[
        ['普通轮','114张，8小时＜剩余期限≤24小时。偏多26、偏空40、中性48；62张因期限≤8小时排除，本篇不再展示其收益。'],
        ['事件与日期','沿用原始176张接管事件，排除47张固定轮。普通轮覆盖58个北京时间入场日期、61个交割日期；114张不等于114个独立市场环境。'],
        ['时间覆盖','普通轮模拟入场从2026年6月19日22:31至9月12日17:11。周末观察从6月20日至9月12日，最后一轮单独保留未到期状态。'],
        ['入场与到期','普通轮用确认后下一分钟Binance BTCUSDT现货开盘，持有到最近一次北京时间16:00，以Deribit官方交割价结算。'],
        ['选腿','最近严格虚值的实际短腿；目标保护宽2000美元，1500／2500对照。仅使用入场前已创建合约；合约存在不等于当时有足量盘口。'],
        ['方向与中性','偏多对应Put、偏空对应Call，均保留相反侧。中性两侧独立统计，没有自动双卖或事后挑赚钱侧。'],
        ['周末例外','周六09:00看此前最新事件，09:01重新定价选腿，周一16:00到期。13个观察点中12个成熟；9月12日轮次未到期，不计成绩。'],
        ['输入边界','沿用9月13日16:16:47冻结范围。价格代理为Binance现货USDT，结算为Deribit美元指数；未逐分钟校正跨市场基差。'],
    ],[90,W-90]))
    story.append(p('资料完整性影响主张范围','h2'))
    story.append(p('当前标准离线评级只读取原卡时点资料。旧卡缺近端分钟证据时仍然缺失；当前模型结论不冒充历史原生结论。所有原始卡、旧评审、上一份PDF和生产程序均保留。'))

    newpage(story,3,'原方向与相反侧','普通轮主情景：胜率、赔付与资金占用要一起看。')
    performance(story,groups('ordinary'),footnote=True)
    story.append(p('方向过滤没有表现为稳定的胜率优势','h2'))
    a=data['ordinary_paired']['directional'];b=data['ordinary_paired']['opposite']
    story.append(p(f"66张有方向卡，原方向侧盈利{a['wins']}/{a['n']}（{pct(a['win_rate'])}），相反侧{b['wins']}/{b['n']}（{pct(b['win_rate'])}）。原方向侧合计净结果较好，但胜率并未更高；偏多和偏空也不是对称关系。"))
    story.append(result_table(['组别','平均盈利 mBTC','平均亏损 mBTC','最差保证金回报','尾部赔付/总补偿'],[
        [label,fnum(m['avg_win_btc']*1000 if m['avg_win_btc'] is not None else None,3),fnum(m['avg_loss_btc']*1000 if m['avg_loss_btc'] is not None else None,3),pct(m['worst_roi']),pct(m['tail_to_credit'])] for label,m in groups('ordinary')
    ],[130,87,87,96,W-400],numeric=(1,2,3,4)))
    story.append(p('1 mBTC＝0.001 BTC。尾部采用该组赔付最大的前5%侧别记录，至少取1笔；比率分母为该组合计假设净补偿。重复侧别、宽度或补偿情景不增加独立样本数。','small'))
    story.append(p('同样66张有方向卡，固定看Put侧的情景胜率为58/66（87.9%），高于跟随原方向的77.3%。普通候选首尾入场价约由63,252升至77,350 USDT，上涨22.3%；两侧差异可能叠加了这段市场背景，不能独立证明方向推断或空间锚创造了优势。'))

    newpage(story,4,'时段与期限组合','左闭右开，北京时间，按模拟入场时刻分组。')
    intervals={'美盘后':'04:00-08:00','亚盘':'08:00-15:00','欧盘':'15:00-20:00','美盘前核心窗':'20:00-21:30','美盘中':'21:30-次日04:00'}
    story.append(result_table(['时段','入场区间','卡数','偏多/偏空/中性','平均剩余期限'],[
        [x['session'],intervals[x['session']],x['cards'],' / '.join(str(x['directions'].get(d,0)) for d in ('BULLISH','BEARISH','NEUTRAL')),fnum(x['put']['dte_mean'])+'小时' if x['cards'] else '无符合期限样本'] for x in data['sessions']
    ],[92,118,38,130,W-378],numeric=(2,)))
    performance(story,[(x['session']+' '+label,x[key]) for x in data['sessions'] if x['cards'] for label,key in [('Put','put'),('Call','call')]])
    story.append(p('亚盘没有符合期限样本，是到期规则与＞8小时过滤共同造成的空组；不能据此说亚盘信号差。“美盘前核心窗”只是研究时段，不代表完整官方盘前。','small'))

    newpage(story,5,'美盘后：优势及其边界','同时看未入选、原方向，以及真实纽约交易日。')
    performance(story,[('美盘后 Put',post['put']),('其余时段 Put',post['not_selected_put']),('美盘后 Call',post['call']),('其余时段 Call',post['not_selected_call'])])
    story.append(p('21/114张入选，覆盖18.4%候选；其余93张完整保留。美盘后实际剩余期限约8.27-11.98小时，其他时段期限更长。这里识别的是可观察的组合条件，不是独立时段因果效应。'))
    story.append(p('各时段都假设收到同样的10%宽度补偿。真实较短期限可能收入更薄；本页优势不能直接转成可成交收益优势。','small'))
    performance(story,[('美盘后原方向',post['directional']),('美盘后相反侧',post['opposite'])])
    ny=[]
    for opened in (True,False):
        for s in ('put_credit','call_credit'):
            g=next(x for x in data['session_ny_groups'] if x['session_bjt']=='美盘后' and x['ny_is_trading_day']==opened and x['side']==s)
            ny.append(('纽约'+('交易日' if opened else '休市日')+' '+LABELS[s],g['metrics']))
    performance(story,ny)
    story.append(p('纽约日期使用夏令时对应关系，剔除周末及6月19日、7月3日、9月7日休市日后，美盘后剩16张。16张Put全盈利是小样本结果，不能转写成必胜规则。[1]','small'))

    newpage(story,6,'周末轮：每周只取一次','来源卡可以陈旧；入场价格和选腿必须重新计算。')
    obs=data['weekend_observations']
    story.append(result_table(['观察周六','来源卡时点','卡龄 小时','原方向','入场 USDT','到期/状态'],[
        [str(x['observation_date_bjt'])[5:],date(x['source_event_ms']),fnum(x['source_card_age_hours']),LABELS.get(x['source_direction'],x['source_direction']),fnum(x.get('entry_price')),date(x['expiry_ms'])+('<br/>未到期' if not x.get('expiry_matured') else '')] for x in obs
    ],[61,91,61,51,94,W-358],numeric=(2,4)))
    story.append(p('共同入场09:01，周一16:00到期，约54小时59分钟。13张来源卡均已包含在114张复评候选中，无额外模型请求。周末引用的是来源卡时评级，不是周六重新评估，更不是55小时持续有效。'))
    story.append(p('7月4日观察点使用约33.4小时前的卡，是唯一卡龄超过24小时的轮次。保留它并给出排除后的对照；24小时只是敏感性界线，不是测得的信号寿命。','small'))

    newpage(story,7,'周末轮：低波动不等于低赔付','12轮成熟样本独立统计；9月12日轮次不进入分母。')
    performance(story,groups('weekend'))
    story.append(p('中性Put是本页最有价值的反例','h2'))
    m=group('weekend','NEUTRAL','put_credit')
    story.append(p(f"7轮中6轮盈利，但唯一净亏损约{m['avg_loss_btc']*1000:.3f} mBTC，最差估算保证金回报{pct(m['worst_roi'])}。合计净结果{mnum(m['total_pnl_btc'])} mBTC。说明高胜率可以与补偿不足同时出现。"))
    performance(story,[('周末原方向',data['weekend_paired']['directional']),('周末相反侧',data['weekend_paired']['opposite'])])
    story.append(p('有方向周末只有5轮，偏空仅2轮，不据此排除某侧。两侧真实报价通常不同，本研究统一补偿只用于识别赔付形态。'))

    # Remaining grade and appendix pages are built below from the same sealed data.
    build_remaining(story,data,main,weekend)
    export(story,data,study,output,scratch)

def build_remaining(story,data,main,weekend,include_ratings=True):
    from astra_second_report import metrics,subset
    newpage(story,8,'周末的波动背景','周六09:01-周一16:00，与其后周二09:01-周四16:00比较。')
    observations=[x for x in data['weekend_observations'] if x['expiry_matured']]
    story.append(result_table(['周六','周末振幅','对照振幅','周末上行幅度','周末下行幅度'],[
        [str(x['observation_date_bjt'])[5:],pct(x['weekend_range_pct']),pct(x['control_range_pct']),pct(x['weekend_up_pct']),pct(x['weekend_down_pct'])] for x in observations
    ],[67,97,97,125,W-386],numeric=(1,2,3,4)))
    v=data['math_summary']['weekend']['volatility_comparison']
    story.append(p(f"12/12个周末的区间振幅较低；平均周末{pct(v['avg_weekend_range_pct'])}，工作日对照{pct(v['avg_control_range_pct'])}。这支持本批存在较低波动背景，但没有测量对应权利金折价，不能推出周末单位风险收入更高。"))
    story.append(p('卡龄敏感性：仅保留24小时以内来源卡','h2'))
    age=[(LABELS[s]+' 全部12轮',metrics(subset(weekend,side=s))) for s in ('put_credit','call_credit')]
    age += [(LABELS[s]+' 卡龄≤24小时',metrics(subset(weekend,side=s,source_card_age_le_24h=True))) for s in ('put_credit','call_credit')]
    performance(story,age)

    newpage(story,9,'净补偿改变多少结果','5%、10%、20%都是假设扣费后收入，不是历史成交报价。')
    story.append(p('净补偿＝比例 × 实际保护宽度／入场价格，单位BTC。提高假设补偿会机械地提高胜率和回报，不能拿20%情景证明市场当时肯支付20%。'))
    for title,key in [('普通轮','credit_groups'),('周末轮','weekend_credit_groups')]:
        story.append(p(title,'h2'));rows=[]
        for d in ('BULLISH','BEARISH','NEUTRAL'):
            for s in ('put_credit','call_credit'):
                gs=[next(x['metrics'] for x in data[key] if x['direction']==d and x['side']==s and x['credit_fraction']==q) for q in (.05,.1,.2)]
                rows.append([LABELS[d]+' '+LABELS[s]]+[pct(m['win_rate'])+'<br/>'+pct(m['capital_roi']) for m in gs])
        tab=result_table(['组别','补偿5%<br/>胜率 / 持有回报','补偿10%<br/>胜率 / 持有回报','补偿20%<br/>胜率 / 持有回报'],rows,[118,131,131,W-380])
        tab.setStyle(TableStyle([('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.append(tab)
    story.append(p('每格第一行是到期净盈利率，第二行是资金加权保证金持有回报。宽度1500／2500对照、实际可用宽度与全部情景保存在逐笔CSV，不重复作为新样本。','small'))

    newpage(story,10,'保证金情景APR怎样阅读','分母来自单例占用校准，不能称为历史实际APR。')
    lam=data['config']['margin']['lambda']
    story.append(p(f"占用系数λ≈{lam:.6f}。每份1 BTC名义合约估算保证金 M＝λ × 实际宽度／入场价格。单笔持有回报＝净盈亏／M；分组APR＝365 × 合计净盈亏／合计（M × 持有天数）。"))
    story.append(p(f"到期没有赔付时，5%／10%／20%补偿分别对应约{pct(.05/lam)}／{pct(.1/lam)}／{pct(.2/lam)}的保证金持有回报；发生赔付后按净额计算。"))
    selected=[('普通原方向',metrics(subset(main,relation='directional'))),('美盘后Put',metrics(subset(main,session_bjt='美盘后',side='put_credit'))),('周末Put',metrics(subset(weekend,side='put_credit'))),('周末Call',metrics(subset(weekend,side='call_credit')))]
    performance(story,selected)
    story.append(p('λ上下浮动25%','h2'))
    story.append(result_table(['组别','估算保证金×0.75','基准','估算保证金×1.25'],[
        [label,pct(m['apr']/.75),pct(m['apr']),pct(m['apr']/1.25)] for label,m in selected
    ],[130,130,100,W-360],numeric=(1,2,3)))
    story.append(p('年化可以很高，但不能外推成账户目标','h2'))
    story.append(p('一笔只持有约10小时的正回报，乘上全年时长会得到很大的APR。这是时间尺度换算：不包含空仓等待、机会稀疏、同时持仓、保证金变化或亏损后的资金约束，也不假设每天有同质量机会。'))
    story.append(p('数量同比放大时，补偿、赔付和估算保证金同比变化，所以该回报率不变；这不代表真实组合保证金与规模永远线性。分组持有回报采用资金加权，不是逐笔百分数的算术平均。'))
    story.append(p('本篇沿用截图的当前权益占用进行单例校准。没有历史逐笔开仓保证金、真实双腿报价或精细账户曲线；不能用一笔实际案例替代这些缺口。','small'))

    if not include_ratings:return
    newpage(story,11,'当前D-S标准的离线复评','历史资料下的当前标准离线评级；不是当时原生评级。')
    c=data['coverage'];calls=data['call_measurements']
    story.append(p(f"114张卡统一使用事实包2.1.1、Prompt2.2.1、输出2.2.0和冻结模型配置。每张同时判断Put、Call，实际共{calls['http_calls']}次HTTP请求；评级先封存，再连接本篇结果。"))
    rows=[]
    for grade in ('D','C','B','A','S','未评级'):
        r=[grade]
        for s in ('put_credit','call_credit'):
            m=next(x['selected'] for x in data['grade_filters'] if x['side']==s and x['filter']==grade)
            r.extend([m['n'],pct(m['win_rate']),pct(m['capital_roi'])])
        rows.append(r)
    story.append(result_table(['等级','Put侧数','Put胜率','Put回报','Call侧数','Call胜率','Call回报'],rows,[55,59,79,79,59,79,W-410],numeric=(1,2,3,4,5,6)))
    for s in ('put_credit','call_credit'):
        unr=c['unrated_counts'][s]
        story.append(p(f"{LABELS[s]}可评级{114-unr}/114（{pct((114-unr)/114)}），未评级{unr}侧。未评级结果单独保留，不当成D，也没有从收益分母中悄悄删除。"))
    story.append(p('本批A和S均为0，无法检验A/S筛选。Put的4个D级全部盈利，也说明小组结果没有形成可靠的单调排序；这4例不足以认定D更好。','small'))
    story.append(p('等级检验的对象','h2'))
    story.append(p('D-S描述该侧侵入风险是否受到可解释约束，不是涨跌强度或胜率概率。同一环境的Put和Call独立判断；本研究检验在固定选腿规则下，高等级组的赔付表现是否改善，不能同时证明具体成交补偿合理。'))
    story.append(p('最近虚值行权价不一定落在锚带或墙位之外。本篇检验的是评级配合这套选腿方法的效用，不能等同于对所有结构用法的全面判定。','small'))
    story.append(p('仅3/114张有原生近端15/30分钟证据。旧卡资料稀疏、缺少字段和未被采纳的回复均如实保留，未用后来数据回填。','small'))

    newpage(story,12,'评级筛选：入选与未选一起看','各侧在同一114张普通轮候选中比较；同一卡两侧不算两个独立市场样本。')
    rows=[]
    for s in ('put_credit','call_credit'):
        for label in ('B及以上','A/S'):
            g=next(x for x in data['grade_filters'] if x['side']==s and x['filter']==label)
            for chosen in ('selected','unselected'):
                m=g[chosen]
                rows.append([LABELS[s]+' '+label+(' 入选' if chosen=='selected' else ' 未选'),m['n'],m['dates'],pct(m['win_rate']),fnum(m['payoff_ratio']),pct(m['capital_roi']),pct(m['apr']),
                             fnum(m['avg_loss_btc']*1000 if m['avg_loss_btc'] is not None else None,3),pct(m['tail_to_credit'])])
    tab=result_table(['筛选组','侧数','不同日期','胜率','盈亏比','持有回报','情景APR','平均亏损<br/>mBTC','尾部赔付<br/>/总补偿'],rows,[105,25,35,44,38,48,55,60,W-410],numeric=(1,2,3,4,5,6,7,8))
    tab.setStyle(TableStyle([('LEFTPADDING',(1,0),(-1,-1),3),('RIGHTPADDING',(1,0),(-1,-1),3)]))
    story.append(tab)
    story.append(p('Put B及以上覆盖54/114（47.4%），Call覆盖45/114（39.5%）；本批它们均为B。Put资金回报约4.4%，未选3.0%；Call约-1.0%，未选-3.8%。改善不等于已经盈利。','small'))
    story.append(p('“未选”包含未达到等级及未评级两类，上一页另列未评级结果。零个A/S意味着本轮无法判断A/S效用。','small'))
    story.append(p('周末只引用来源卡时等级','h2'))
    rows=[]
    for s in ('put_credit','call_credit'):
        for label in ('B及以上','A/S'):
            g=next(x for x in data['weekend_grade_filters'] if x['side']==s and x['filter']==label)
            rows.append([LABELS[s]+' '+label,g['selected']['n'],pct(g['selected']['win_rate']),pct(g['selected']['capital_roi']),g['unselected']['n'],pct(g['unselected']['win_rate'])])
    story.append(result_table(['周末来源评级','入选轮数','入选胜率','持有回报','未选轮数','未选胜率'],rows,[116,63,78,85,74,W-416],numeric=(1,2,3,4,5)))
    story.append(p('评级时点与周末入场存在卡龄；这里不把旧卡较高等级理解为周六仍成立。只有12个成熟周末，不能凭少量筛选后的轮次识别稳定等级优势。'))
    story.append(p('周末没有一致的B级优势：Put B组6/7盈利，未选5/5；Call两组均为75%。Put B组还包含那笔主要尾部亏损。','small'))

    newpage(story,13,'评级差异来自哪里','先检查原方向和资料覆盖，不把等级与收益的相关当作独立因果。')
    story.append(result_table(['原方向/侧别','全部侧数','全部胜率','B及以上侧数','入选胜率','未选胜率'],[
        [LABELS[d]+' '+LABELS[s],len(a),pct(metrics(a)['win_rate']),len(b),pct(metrics(b)['win_rate']),pct(metrics([x for x in a if x not in b])['win_rate'])]
        for d in ('BULLISH','BEARISH','NEUTRAL') for s in ('put_credit','call_credit')
        for a in [[x for x in main if x['direction']==d and x['side']==s]]
        for b in [[x for x in a if x['offline_grade'] in ('B','A','S')]]
    ],[111,60,73,96,80,W-420],numeric=(1,2,3,4,5)))
    story.append(p('资料覆盖与评级可得性','h2'))
    rows=[]
    for key,label in [('source_walls_present','原卡墙位'),('source_has_near_term','原生近端证据')]:
        for available in (True,False):
            base=[x for x in main if x[key]==available]
            n=len({x['card_id'] for x in base});values=[]
            for s in ('put_credit','call_credit'):
                side=[x for x in base if x['side']==s]
                values += [sum(x['offline_grade'] is not None for x in side),sum(x['offline_grade'] in ('B','A','S') for x in side)]
            rows.append([label+('有' if available else '无'),n,*values])
    story.append(result_table(['资料分组','卡数','Put可评级','Put B及以上','Call可评级','Call B及以上'],rows,[116,44,83,90,83,W-416],numeric=(1,2,3,4,5)))
    story.append(p('Put B的提升主要见于原中性组；原偏多、偏空组并未一致改善。Call B在原偏多和偏空组胜率较高，中性组却低于未选。整体差异不能直接归功于字母等级。'))
    story.append(p('资料和时间阶段也有影响。有墙位的Call组中，B的资金回报约-0.5%，未选约-0.2%，并未更好；原卡7月的Call B回报弱于未选，9月则较好。所有3张原生近端卡均得到Call B，且集中在最后阶段。'))
    story.append(p('“墙位有”仅表示字段存在，不等于来源有效。月份、时段和资料覆盖的详表随CSV交付；交叉组偏小，尚不能分离这些因素的独立影响。','small'))
    story.append(p('27个未评级侧分布在23张卡。已核实事实编号误写，以及两例“不是收益概率／不是胜率”的否定句被保守文本检查误拦；其他失败还包括路径断言和说明格式。未评级不能笼统归因于缺数据，原回复及校验原因均保留。','small'))

    newpage(story,14,'需要保留的证据边界','把下一步缩小到可验证的一项筛选，不增加指标或半级评分。')
    story.append(p('评级、完整度与时间阶段','h2'))
    story.append(p('评级结果同时附带原方向、月份、时段、是否有墙位及原生近端证据。资料完整度随版本和时间变化，等级差异可能反映“能够判断多少”，不一定反映独立的交易优势。分组明细随结果包保留。'))
    story.append(p('当前研究能够支持的下一步','h2'))
    story.append(result_table(['方向','下一项应保留的检验'],[
        ['美盘后','将04:00-08:00与约8-12小时剩余期限作为联合观察条件。继续保留相反侧，不直接跟随原看涨／看跌标签。'],
        ['周末轮','低振幅迹象成立于本批；先检查一笔较大赔付需要多少轮收入才能弥补，并保留来源卡龄。不能仅凭周末低波动增加仓位。'],
        ['评级用途','本批支持B的局部筛选迹象，尚不足以作为单独硬过滤。先核对引用误写及否定句误拦；本篇不恢复字母或重跑。A/S无样本，不按历史盈亏降门槛或制造B+。'],
        ['真实补偿','下一批只补少量实际两腿净权利金与新增保证金记录，就能检验本篇5%／10%／20%假设是否接近可成交条件；不必立即建设完整历史盘口系统。'],
    ],[91,W-91]))
    story.append(p('本批没有非接管信号对照，也没有真实双腿净报价，因此尚不能确认空间锚的独立增量或可成交的真实收益。当前得到的是值得继续验证的用途迹象。'))
    story.append(p('复评负载与可追溯性','h2'))
    story.append(p(f"实际请求{calls['http_calls']}次，其中两次JSON语法恢复；输入合计约{calls['input_bytes_sum']/1024/1024:.2f} MiB。返回总token为{calls['usage'].get('total_tokens',0):,}，平均请求耗时{calls['elapsed_mean_seconds']:.1f}秒。请求配置为deepseek-v4-flash，116份响应均报告deepseek-flash；这是提供商返回名称，不冒充固定权重版本。总额度228、并发2，未为等级或文案重试。",'small'))
    story.append(p('数据与方法来源','h2'))
    for label,url in [('[1] NYSE：2026年交易时间与休市日','https://www.nyse.com/trade/hours-calendars'),
        ('[2] Deribit：币本位期权、交割与赔付','https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options'),
        ('[3] Binance：公开分钟线资料','https://github.com/binance/binance-public-data')]:
        story.append(p(f'<link href="{url}" color="#3D4E5B"><u>{label}</u></link>','small'))
    story.append(p('PDF附件含中文主情景逐笔表、全部补偿／宽度结果和固定研究口径；独立结果包另含封存评审、失败归因及可复现代码。原生产系统和上一份PDF没有改写。','small'))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--scratch',type=Path,required=True)
    args=parser.parse_args();build(args.study,args.output,args.scratch)
