"""Neutral-paper PDF from the frozen Astra study. No production imports or network."""
import argparse
import csv
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from pypdf import PdfReader, PdfWriter

INK = colors.HexColor("#202326")
MUTED = colors.HexColor("#596067")
RULE = colors.HexColor("#D6D8D8")
PALE = colors.HexColor("#F5F5F3")
LOSS = colors.HexColor("#813B3C")
PAGE_W, PAGE_H = A4
MARGIN = 42
WIDTH = PAGE_W - MARGIN * 2


def register_fonts():
    pdfmetrics.registerFont(TTFont("CN", "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("CNB", "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("NUM", "C:/Windows/Fonts/arial.ttf"))
    pdfmetrics.registerFontFamily("CN", normal="CN", bold="CNB", italic="CN", boldItalic="CNB")


def styles():
    base = dict(fontName="CN", textColor=INK, wordWrap="CJK", spaceAfter=8)
    return {
        "title": ParagraphStyle("title", **base, fontSize=25, leading=35, spaceBefore=8),
        "h1": ParagraphStyle("h1", **base, fontSize=19, leading=28),
        "h2": ParagraphStyle("h2", **base, fontSize=12, leading=19, spaceBefore=11),
        "body": ParagraphStyle("body", **base, fontSize=10, leading=17),
        "small": ParagraphStyle("small", fontName="CN", fontSize=8.5, leading=13.5, textColor=MUTED, wordWrap="CJK", spaceAfter=6),
        "cell": ParagraphStyle("cell", **base, fontSize=8.6, leading=13),
        "head": ParagraphStyle("head", fontName="CNB", fontSize=8.2, leading=12, textColor=MUTED, wordWrap="CJK"),
        "ledger": ParagraphStyle("ledger", **base, fontSize=8.4, leading=12.4),
        "num": ParagraphStyle("num", fontName="NUM", fontSize=9, leading=13, alignment=TA_RIGHT, textColor=INK),
        "num_cn": ParagraphStyle("num_cn", fontName="CN", fontSize=8.6, leading=13, alignment=TA_RIGHT, textColor=INK),
    }


class Rule(Flowable):
    def __init__(self, width=WIDTH, gap=12):
        super().__init__(); self.width = width; self.height = gap
    def draw(self):
        self.canv.setStrokeColor(RULE); self.canv.setLineWidth(.5)
        self.canv.line(0, self.height / 2, self.width, self.height / 2)


class Bookmark(Flowable):
    def __init__(self, name, title):
        super().__init__(); self.name = name; self.title = title; self.width = self.height = 0
    def draw(self):
        self.canv.bookmarks.append((self.title, self.canv._pageNumber - 1))


class NumberedCanvas(Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.saved = []; self.bookmarks = []
    def showPage(self):
        self.saved.append(dict(self.__dict__)); self._startPage()
    def save(self):
        total = len(self.saved)
        for state in self.saved:
            self.__dict__.update(state)
            self.setStrokeColor(RULE); self.setLineWidth(.5)
            self.line(MARGIN, 34, PAGE_W - MARGIN, 34)
            self.setFillColor(MUTED); self.setFont("CN", 7.4)
            self.drawString(MARGIN, 22, "Astra  /  接管信号研究  /  盈亏平衡侵入口径")
            self.setFont("NUM", 8)
            self.drawRightString(PAGE_W - MARGIN, 22, f"{self._pageNumber:02d} / {total:02d}")
            super().showPage()
        super().save()


def header(canvas, doc):
    canvas.saveState(); canvas.setFillColor(MUTED); canvas.setFont("NUM", 7.5)
    canvas.drawString(MARGIN, PAGE_H - 28, "ASTRA  /  RESEARCH NOTE")
    canvas.setFont("CN", 7.3)
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 28, "2026.09.13  ·  中性纸面")
    canvas.restoreState()


def pct(value):
    return "-" if value is None else f"{value * 100:.1f}%"


def ratio(value):
    return "无亏损" if value is None else f"{value:.3f}"


def money(value):
    return f"{value:,.2f}"


def p(text, style="body"):
    return Paragraph(text, STYLES[style])


def table(headers, rows, widths, numeric=()):
    content = [[p(h, "head") for h in headers]]
    for row in rows:
        content.append([cell if isinstance(cell, Paragraph) else p(str(cell), ("num" if str(cell).isascii() else "num_cn") if j in numeric else "cell") for j, cell in enumerate(row)])
    tab = Table(content, colWidths=widths, repeatRows=1, hAlign="LEFT")
    tab.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), PALE),
        ("LINEBELOW", (0, 0), (-1, 0), .7, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), .35, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tab


def section(story, number, title, subtitle=None):
    story.extend([Bookmark(f"section-{number}", title), p(f"{number:02d}  {title}", "h1")])
    if subtitle:
        story.append(p(subtitle, "small"))
    story.append(Rule())


def overview_group(overview, direction, side, q=.1):
    return next(g for g in overview["groups"] if g["direction"] == direction and g["side"] == side and g["credit_fraction"] == q)


def monthly_chart(samples):
    counts = Counter(datetime.fromtimestamp(int(s["event_time_ms"]) / 1000, timezone(timedelta(hours=8))).strftime("%Y-%m") for s in samples)
    months = sorted(counts)
    drawing = Drawing(WIDTH, 135)
    chart = HorizontalBarChart(); chart.x = 50; chart.y = 18; chart.width = WIDTH - 95; chart.height = 103
    chart.data = [[counts[m] for m in months]]
    chart.categoryAxis.categoryNames = [m.replace("2026-", "") + "月" for m in months]
    chart.categoryAxis.labels.fontName = "CN"; chart.categoryAxis.labels.fontSize = 8.5
    chart.categoryAxis.strokeColor = RULE
    chart.valueAxis.valueMin = 0; chart.valueAxis.valueMax = 70; chart.valueAxis.valueStep = 20
    chart.valueAxis.labels.fontName = "NUM"; chart.valueAxis.labels.fontSize = 7.5
    chart.valueAxis.strokeColor = RULE
    chart.bars[0].fillColor = colors.HexColor("#676C70"); chart.bars[0].strokeColor = None
    chart.barLabelFormat = "%d"; chart.barLabels.fontName = "NUM"; chart.barLabels.fontSize = 9
    chart.barLabels.nudge = 9
    drawing.add(chart)
    drawing.add(String(WIDTH - 36, 3, "事件数", fontName="CN", fontSize=7, fillColor=MUTED))
    return drawing


def ledger_side(item, main):
    value = item["net_pnl_btc"] * 1000
    loss = item["breakeven_intruded"]
    label = "亏 / 侵入" if loss else "平" if item["result"] == "tie" else "盈"
    tone = "#813B3C" if loss else "#202326"
    role = " · 主" if main else ""
    return p(
        f"{item['short_strike']:,.0f} / {item['long_strike']:,.0f}<br/>"
        f"平衡 {item['near_breakeven_price']:,.2f}<br/>"
        f"<font color='{tone}'><b>{value:+.4f}  {label}</b></font>{role}", "ledger")


def build(study, projection, output, scratch):
    global STYLES
    register_fonts(); STYLES = styles()
    overview = json.loads((study / "research_overview.json").read_text(encoding="utf-8"))
    projected = json.loads(projection.read_text(encoding="utf-8"))
    ledger = projected["chronological_ledger"]
    assert len(ledger) == 176 and len({r["card_id"] for r in ledger}) == 176
    with (study / "standard_signal_samples.csv").open(encoding="utf-8-sig") as stream:
        samples = list(csv.DictReader(stream))
    by_id = {r["card_id"]: r for r in samples}
    groups = [("偏多 · Put", "BULLISH", "put_credit"), ("偏空 · Call", "BEARISH", "call_credit"), ("中性 · Put", "NEUTRAL", "put_credit"), ("中性 · Call", "NEUTRAL", "call_credit")]
    story = []

    # 1: the conclusion and compact decision-relevant scoreboard.
    story.extend([Bookmark("conclusion", "研究结论"), p("176张接管信号\n<br/>短期期权适配研究", "title"), p("以盈亏平衡定义侵入 · 逐笔结果与初步效用判断", "small"), Rule( gap=19)])
    story.append(p("<b>偏多Put有局部效用迹象；整体方向筛选尚未证明稳定增益。</b>", "h2"))
    story.append(p("偏多信号对应Put信用价差较好，偏空信号对应Call的结果较弱。合并后，原方向没有超过同批固定Put基准。空间锚的独立增益、真实可成交盈利仍未得到证明。"))
    story.append(p("主情景：目标保护宽度2000美元，净补偿为实际宽度入场折算价值的10%；全部持有至最近一次北京时间16:00，期限不超过24小时。", "small"))
    rows = []
    for label, direction, side in groups:
        g = overview_group(overview, direction, side)
        wins = round(g["win_rate"] * g["n"]); ties = round(g["tie_rate"] * g["n"]); losses = g["n"] - wins - ties
        rows.append([label, g["n"], f"{wins} / {losses} / {ties}", pct(g["win_rate"]), pct(losses / g["n"]), ratio(g["payoff_ratio"])])
    story.append(table(["信号与侧别", "张数", "盈 / 亏 / 平", "情景胜率", "侵入率", "盈亏比"], rows, [117, 38, 91, 78, 78, WIDTH - 402], numeric=(1, 3, 4, 5)))
    story.append(Spacer(1, 12))
    story.append(p("<b>侵入＝到期两腿净赔付超过净收权利金。</b> 恰好盈亏平衡单列；期间触及行权价不判负。这里的侵入已经考虑补偿，不再沿用旧报告的短腿行权价口径。"))
    story.append(p("胜率＝盈利笔数 / 可计算笔数；盈亏比＝平均盈利 / 平均亏损绝对值。小于1表示一次平均亏损大于一次平均盈利，仍需结合胜率判断。", "small"))
    story.append(p("95张有方向信号按原方向侧：75盈、20亏、0平，情景胜率78.9%。81张中性信号的Put、Call分别呈现，不合成双卖策略，也不事后选择赚钱侧。"))
    story.append(p("<b>阅读顺序</b>　时间覆盖与样本 → 补偿及盈亏平衡 → 对照与期限 → 尾部及下一步 → 176张逐笔附录。", "small"))
    story.append(p("本报告使用真实信号、历史合约元数据与官方交割价，净权利金为研究假设。10%补偿不等于保证金收益10%，也不是实盘胜率认证。", "small"))
    story.append(PageBreak())

    # 2: coverage and provenance, including the absence of a no-signal control.
    section(story, 1, "样本覆盖与时间间隔", "时间统一显示为北京时间；87个自然日中，77天出现研究信号。")
    story.append(table(["时间与数量", "本次冻结范围"], [
        ["第一张信号", "2026-06-19 15:04:35"], ["最后一张信号", "2026-09-13 09:31:11"],
        ["首末实际跨度", "85天18小时26分35秒"], ["冻结截止", "2026-09-13 16:16:47"],
        ["相邻事件间隔", "中位数8.09小时；最短18.71分钟；最长65.26小时"],
        ["模拟持有期限", "9分钟至23小时51分钟；全部已到期"],
    ], [133, WIDTH - 133]))
    story.append(p("按月出现的接管信号", "h2")); story.append(monthly_chart(samples))
    story.append(p("6月和9月为部分月份。事件之间没有卡，不代表行情缺失；本次已对齐124336根连续现货分钟线。事件分布也不等于市场全部机会或模型漏信号率。", "small"))
    story.append(table(["来源层", "保留的证据"], [
        ["信号", "223张唯一真实卡中排除47固定轮，保留176事件；偏多41、偏空54、中性81。原方向来自当时最终判断，UP / DOWN冲击不充当预测。"],
        ["市场与合约", "Binance现货分钟线；Deribit研究期4616条合约元数据、87日官方交割价。按入场时已创建、指定到期筛选实际合约。"],
        ["完整性", "176张到期结果可算，176条分钟路径完整。1条损坏记录、1条完全重复和1张模拟卡分别登记。"],
    ], [83, WIDTH - 83]))
    story.append(p("版本从1.3.0至1.6.2，字段统一但不抹去版本和日期差异。所有样本均已发生接管，缺少同背景的非信号时点对照。", "small"))
    story.append(PageBreak())

    # 3: define credit, breakeven, and the BTC inverse settlement qualification.
    section(story, 2, "侵入与净补偿的新口径", "先定义净盈亏，再判断侵入；不能只把美元权利金加减到行权价。")
    story.append(p("<b>净结果＝净收权利金－两腿到期净赔付。</b><br/>净结果大于0为盈利，小于0为侵入且亏损，等于0为平衡。本批主情景没有恰好平衡的样本。"))
    story.append(table(["条件", "本文的含义"], [
        ["净补偿5% / 10% / 20%", "按实际价差宽度的入场折算价值计算，假设已经扣费。没有真实双腿成交价，不声称当时能取得该补偿。"],
        ["单笔保证金收益10%", "分母是实际占用保证金，与价差宽度不同。本次没有逐笔保证金记录，不能把两种10%直接等同。"],
        ["最近严格虚值卖出腿", "按确认后下一分钟现货开盘，在当时已创建的同到期合约中选腿；这不是根据后续行情选出的有利行权价。"],
    ], [139, WIDTH - 139]))
    story.append(p("币本位计算方式", "h2"))
    story.append(p("以每份1 BTC合约为单位，记入场基准价为P₀，交割价为S，实际两腿宽度为W，补偿比例为q：<br/><b>净补偿 c＝q × W / P₀（BTC）。</b>"))
    story.append(p("Put赔付＝[max(卖出价－S, 0)－max(保护价－S, 0)] / S<br/>Call赔付＝[max(S－卖出价, 0)－max(S－保护价, 0)] / S", "small"))
    story.append(p("在卖出腿与保护腿之间，近端盈亏平衡价为：<br/><b>Put：卖出行权价 / (1＋c)；Call：卖出行权价 / (1－c)。</b>"))
    story.append(p("附录展示这个近端平衡价；最终盈亏始终按完整两腿赔付计算。特别是币本位Call价差，价格超过保护腿后赔付为W / S，极端高价下可能再回到盈利，不能把“高于近端平衡价”当作全局亏损条件。[1]", "small"))
    story.append(p("怎样读逐笔金额", "h2"))
    story.append(p("附录净盈亏统一用<b>mBTC</b>：1 mBTC＝0.001 BTC，按每份1 BTC名义合约计算。它们是等量研究结果，不是你的实际仓位或保证金回报；重叠事件也没有连成账户资金曲线。"))
    story.append(p("例如实际宽度2000美元、入场价80000，10%净补偿为0.0025 BTC，即2.5 mBTC。到期赔付低于2.5 mBTC仍盈利，即使卖出腿已经被轻微打入。"))
    story.append(PageBreak())

    # 4: paired comparisons and explicit parameter sensitivity.
    section(story, 3, "方向筛选与补偿敏感性", "主宽度2000美元；所有对照面对同卡之后的行情。")
    rows=[]
    for label, direction, side in groups:
        rates=[]
        for q in (.05,.1,.2):
            g=overview_group(overview,direction,side,q); rates.append(f"{pct(g['win_rate'])} / {ratio(g['payoff_ratio'])}")
        rows.append([label,*rates])
    story.append(table(["研究侧", "5%：胜率 / 盈亏比", "10%：胜率 / 盈亏比", "20%：胜率 / 盈亏比"], rows, [105,135,135,WIDTH-375]))
    story.append(p("同一批95张有方向信号的固定侧别基准", "h2"))
    rows=[]
    for label,key in [("按原方向选择","original_direction"),("固定Put侧","always_put"),("固定Call侧","always_call")]:
        g=next(x for x in overview['fixed_side_benchmarks'] if x['benchmark']==key)
        rows.append([label,g['n'],pct(g['win_rate']),pct(1-g['win_rate']-g['tie_rate']),ratio(g['payoff_ratio'])])
    story.append(table(["规则", "张数", "10%情景胜率", "侵入率", "盈亏比"],rows,[148,49,110,103,WIDTH-410],numeric=(1,2,3,4)))
    story.append(p("固定Put仅为同时报告的对照，不是未来全部卖Put的建议。原方向未超过这一基准，部分优势可能来自本批行情更有利于Put侧。", "small"))
    story.append(p("按交割日平衡事件聚集后", "h2"))
    rows=[]
    for label,key in [("偏多：Put减Call","BULLISH"),("偏空：Call减Put","BEARISH"),("合并：原侧减反侧","ALL_DIRECTIONAL")]:
        g=next(x for x in overview['paired_day_sensitivity'] if x['direction']==key and x['width_selection']=='all_actual_widths')
        rows.append([label,f"{g['n']} / {g['delivery_days']}",f"{g['equal_card_mean_difference_btc']*1000:+.3f}",f"{g['equal_delivery_day_mean_difference_btc']*1000:+.3f}"])
    story.append(table(["同卡净结果差", "卡数 / 交割日", "每卡等权 mBTC", "每日等权 mBTC"],rows,[161,99,124,WIDTH-384],numeric=(2,3)))
    story.append(p("先平均同一交割日内的配对差，再平均各日。合并优势由微正变负；偏多优势与偏空劣势仍保留。两侧实际均为2000宽的子集中，多空不对称也仍在。", "small"))
    story.append(p("本批累计持平所需补偿比例约为：偏多Put 3.0%、偏空Call 11.6%、中性Put 7.6%、中性Call 7.4%。这是历史赔付摘要，不是新的开仓门槛。", "small"))
    story.append(PageBreak())

    # 5: tenor, width and tails, no false lifetime inference.
    section(story, 4, "期限与尾部赔付", "按剩余期限观察适配，不把短期触及判负，也不宣称测出了信号寿命。")
    rows=[]
    for bucket,label in [("(0,4]","0-4小时"),("(4,8]","4-8小时"),("(8,16]","8-16小时"),("(16,24]","16-24小时")]:
        g=next(x for x in overview['tenors'] if x['dte_bucket']==bucket and x['relation']=='directional')
        rows.append([label,g['n'],pct(g['win_rate']),pct(1-g['win_rate']-g['tie_rate']),ratio(g['payoff_ratio'])])
    story.append(table(["原方向侧期限", "张数", "10%情景胜率", "侵入率", "盈亏比"],rows,[130,49,115,103,WIDTH-397],numeric=(1,2,3,4)))
    story.append(p("8-16小时值得优先观察，0-4小时仅12张，不能因全胜认定最好。统一补偿比例并不反映近到期期权实际报价，不能据此生成硬性期限过滤。"))
    story.append(p("中性两侧的期限差异", "h2"))
    rows=[]
    for bucket,label in [("(0,4]","0-4小时"),("(4,8]","4-8小时"),("(8,16]","8-16小时"),("(16,24]","16-24小时")]:
        put=next(x for x in overview['tenors'] if x['dte_bucket']==bucket and x['relation']=='neutral_put')
        call=next(x for x in overview['tenors'] if x['dte_bucket']==bucket and x['relation']=='neutral_call')
        rows.append([label,put['n'],pct(put['win_rate']),pct(call['win_rate'])])
    story.append(table(["期限", "每侧张数", "Put情景胜率", "Call情景胜率"],rows,[160,95,125,WIDTH-380],numeric=(1,2,3)))
    story.append(p("全体仅1张中性卡剩9分钟，已进入到期前30分钟交割均价窗口；保留该卡，但不把它当作完整未来路径的预测。", "small"))
    story.append(p("少数较大赔付仍会吞掉多次收入", "h2"))
    rows=[]
    for label,direction,side in groups:
        g=overview_group(overview,direction,side)
        rows.append([label,f"{math.ceil(g['n']*.05)} / {g['n']}",pct(g['tail_top_5pct_payout_to_total_credit'])])
    story.append(table(["组别", "最大赔付约5%样本", "占本组全部假设净补偿"],rows,[165,149,WIDTH-314],numeric=(2,)))
    story.append(p("以上按10%补偿，5%时比例翻倍。一次亏损可能抵掉多次小赚，不能只看胜率。", "small"))
    story.append(PageBreak())

    # 6: equal-contract monetary results, separate from account returns.
    section(story, 5, "金额汇总与宽度对照", "金额统一为mBTC，每次一份1 BTC名义合约；不是保证金回报或账户资金曲线。")
    rows=[]
    for label,direction,side in groups:
        g=overview_group(overview,direction,side)
        credit=g['total_credit_btc']*1000; payout=g['total_payout_btc']*1000
        rows.append([label,f"{credit:.3f}",f"{payout:.3f}",f"{credit-payout:+.3f}",f"{g['avg_win_btc']*1000:.3f} / {g['avg_abs_loss_btc']*1000:.3f}"])
    story.append(table(["主情景组别", "净补偿合计", "赔付合计", "净结果合计", "平均盈利 / 平均亏损"],rows,[105,85,85,85,WIDTH-360],numeric=(1,2,3)))
    g=next(x for x in overview['width_sensitivity'] if x['target_width']==2000 and x['direction']=='ALL_DIRECTIONAL')
    net=(g['total_credit_btc']-g['total_payout_btc'])*1000
    story.append(p(f"95张原方向侧合计净结果<b>{net:+.3f} mBTC</b>，由偏多侧盈利抵消偏空侧亏损形成。中性两侧各自合计，不能将四行相加当成176笔单一策略。"))
    story.append(p("等量累计为正不证明信号筛选带来了增益：前页的同卡固定侧别与按日比较仍必须保留。没有真实净权利金、逐笔占用保证金及并发持仓，不能将本表换算成年化收益或实盘账户回报。", "small"))
    story.append(p("仅改变目标保护宽度：95张原方向侧", "h2"))
    rows=[]
    for width in (1500,2000,2500):
        g=next(x for x in overview['width_sensitivity'] if x['target_width']==width and x['direction']=='ALL_DIRECTIONAL')
        rows.append([f"{width:,}美元",g['n'],pct(g['win_rate']),ratio(g['payoff_ratio']),f"{(g['total_credit_btc']-g['total_payout_btc'])*1000:+.3f}"])
    story.append(table(["目标宽度", "张数", "10%情景胜率", "盈亏比", "净结果合计 mBTC"],rows,[120,50,115,90,WIDTH-375],numeric=(1,2,3,4)))
    story.append(p("这里固定的是净补偿比例，宽度增加时假设收入也增加。因此更宽后的表面改善包含补偿假设的影响，不能据此认定保护腿越远越优。实际合约没有目标宽度时使用真实可用宽度，逐笔表可由两腿价格核对。"))
    story.append(p("主情景为2000美元目标宽度。1500和2500是敏感性对照，重复宽度、补偿档位和两侧都不是新增独立信号样本。", "small"))
    story.append(PageBreak())

    # 7: user-provided real position example, no inferred fills or margin history.
    section(story, 6, "真实案例：两种回报率怎样对应", "据用户提供的持仓截图估算；该案例单独解释口径，不加入176张历史结果。")
    story.append(table(["截图信息", "读数"],[
        ["同到期Call信用价差", "2026-09-14到期；卖出77500 Call、买入79000 Call，各0.3份；宽度1500美元。"],
        ["两腿平均价格", "卖出0.0049 BTC；买入0.0011 BTC（均为每份报价）。"],
        ["权益及占用", "顶部显示0.0126 BTC，初始保证金占用率44.24%；按该数值为对应权益估算。"],
        ["当前标价", "卖出腿0.0018 BTC，保护腿0.0002 BTC；标的现价约76826。"],
    ],[119,WIDTH-119]))
    story.append(p("按账户权益占用比例计算", "h2"))
    story.append(p("权利金差＝(0.0049－0.0011) × 0.3＝<b>0.00114 BTC</b>。<br/>当前初始保证金≈0.0126 × 44.24%＝<b>0.00557424 BTC</b>。"))
    story.append(table(["回报口径", "近似结果", "适用边界"],[
        ["最大毛收益 / 当前保证金", "20.5%", "到期无赔付时全部权利金留存；未扣开仓与结算等费用。"],
        ["最大毛收益 / 当前账户权益", "9.0%", "分母为当前显示权益，不是开仓前账户权益。"],
        ["标价浮盈 / 当前保证金", "11.8%", "标价浮盈约0.00066 BTC；不是可成交平仓收益。"],
    ],[177,66,WIDTH-243],numeric=(1,)))
    story.append(p("用户已说明挂单是卖出腿的买回止盈单，本例按只减少既有仓位处理，不另计新增保证金。组合保证金仍会随价格、波动和持仓变化；本页使用当前占用估算，尚不能还原开仓时的新增保证金。[4]", "small"))
    story.append(p("对应研究中的哪档补偿", "h2"))
    story.append(p("用截图现价暂代入场标的价：0.00114 / (1500 × 0.3 / 76826)≈<b>19.5%</b>。这更接近研究20%档，和“账户权益回报约9%”可以同时成立，因为分母不同。精确对应仍需实际入场标的价和费用；不能用一笔案例替全部历史卡确定补偿。"))
    story.append(p("该结构的近端盈亏平衡价，按每份权利金差0.0038 BTC，约为77500 / (1－0.0038)＝<b>77795.62美元</b>，尚未扣费；最终结果仍按币本位两腿赔付计算。"))
    story.append(p('<link href="https://support.deribit.com/hc/en-us/articles/25944811317149-Margin-types-and-usage" color="#3D4E5B"><u>[4] Deribit：保证金模式、权益与组合保证金</u></link>',"small"))
    story.append(PageBreak())

    # 8: bounded interpretation, sources, and reading the complete ledger.
    section(story, 7, "证据边界与下一步", "先检验一项可解释的筛选假设，不从历史分组中搜索最佳参数。")
    story.append(table(["已知条件", "可以得出的判断"], [
        ["锚带内172张", "只有3张带外、1张带宽受限，难以检验带内相对带外的增益；没有非接管对照，更不能独立证明Anchor有效。"],
        ["Gamma背景", "95张有方向样本：过渡83、正Gamma钉住6、负Gamma放大6。小组太少，不据此调权。GGR状态与看板净GEX分开。"],
        ["量价与主动流", "有方向样本：同向60、分歧5、其余30。10%情景胜率73.3%、100.0%、86.7%；不能奖励只有5张的分歧组，也不能认定同向越多越安全。"],
        ["保护宽度", "主目标2000的352个侧别中，293个实际2000、55个1500、3个1000、1个2500。更宽时假设补偿线性增加，不等于真实报价更划算。"],
    ],[102,WIDTH-102]))
    story.append(p("优先核对偏空信号的上行侵入反例", "h2"))
    story.append(p("偏空可能描述了已经发生的下行，却未约束接下来向上反弹。先检查54张偏空卡中的代表性失败：上方空间、结构位置和价格响应是否已有反证。原因尚未证实，先形成一项卡时可判断的假设，再用新样本验证。"))
    story.append(p("偏多Put保留为研究主线，8-16小时只作为观察分层。不直接修改评级、Prompt或交易权限。若需要确认真实可成交效用，优先补少量真实两腿净权利金及新增保证金记录。"))
    story.append(p("逐笔附录怎样读", "h2"))
    story.append(p("按176张确认时间排序，每张同列Put与Call。侧别栏依次显示卖出／保护行权价、近端盈亏平衡价、净盈亏mBTC；“主”标明原方向侧，非交易许可。中性两侧分别保留。原卡ID及完整精度随PDF附带的CSV保存。", "small"))
    story.append(p("数据与方法来源", "h2"))
    for text,url in [
        ("[1] Deribit：BTC币本位期权、交割价与结算公式", "https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options"),
        ("[2] Deribit：历史合约元数据接口", "https://docs.deribit.com/api-reference/market-data/public-get_instruments"),
        ("[3] Binance：公开分钟线归档与时间单位", "https://github.com/binance/binance-public-data"),
    ]:
        story.append(p(f'<link href="{url}" color="#3D4E5B"><u>{text}</u></link>', "small"))
    story.append(p("沿用9月13日冻结原档和市场数据；没有重评历史LLM或更改原卡。合约列表支持研究内选腿，不证明当时的双边盘口、流动性或保证金状态。入场采用Binance BTCUSDT代理，交割用Deribit美元指数，未逐分钟校正跨市场基差。", "small"))
    story.append(PageBreak())

    # One record per signal, both sides always visible.
    labels = {"BULLISH":"偏多", "BEARISH":"偏空", "NEUTRAL":"中性"}
    per_page = 12
    for start in range(0,len(ledger),per_page):
        chunk=ledger[start:start+per_page]
        story.append(Bookmark(f"ledger-{start+1}",f"逐笔 {start+1:03d}-{start+len(chunk):03d}"))
        story.append(p(f"逐笔记录  {start+1:03d}-{start+len(chunk):03d}", "h1"))
        story.append(p("2026年 · 北京时间 · 目标宽2000美元 / 净补偿10%情景 · 净盈亏单位mBTC", "small"))
        rows=[]
        for item in chunk:
            signal=datetime.fromisoformat(item['signal_time_bjt'])
            expiry=datetime.fromisoformat(item['expiry_time_bjt'])
            rows.append([
                f"{item['seq']:03d}",
                signal.strftime("%m/%d<br/>%H:%M:%S"),
                labels[item['direction']],
                f"{money(item['entry_price'])}<br/>{money(item['delivery_price'])}",
                expiry.strftime("%m/%d %H:%M")+f"<br/>{item['dte_hours']:.2f}小时",
                ledger_side(item['put_credit'], item['direction']=='BULLISH'),
                ledger_side(item['call_credit'], item['direction']=='BEARISH'),
            ])
        tab=table(["编号","信号时点","方向","入场 USDT<br/>交割 USD","到期 / 期限","Put：卖 / 保<br/>平衡价 / 净盈亏","Call：卖 / 保<br/>平衡价 / 净盈亏"],rows,[27,65,31,68,65,127,WIDTH-383])
        tab.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,1),(-1,-1),6),('BOTTOMPADDING',(0,1),(-1,-1),6)]))
        story.append(tab)
        story.append(Spacer(1,7))
        story.append(p("平衡价显示为近端根；侵入标记始终以完整两腿净结果为准。正号为净盈利，负号为净亏损。实际宽度由卖／保行权价之差确定。", "small"))
        if start+per_page<len(ledger):story.append(PageBreak())

    scratch.mkdir(parents=True, exist_ok=True); output.parent.mkdir(parents=True, exist_ok=True)
    raw_pdf=scratch/'astra-study-layout.pdf'
    doc=SimpleDocTemplate(str(raw_pdf),pagesize=A4,rightMargin=MARGIN,leftMargin=MARGIN,topMargin=47,bottomMargin=47,title="Astra 176张接管信号短期期权适配研究",author="Astra",pageCompression=1)
    doc.build(story,onFirstPage=header,onLaterPages=header,canvasmaker=NumberedCanvas)
    reader=PdfReader(str(raw_pdf));writer=PdfWriter();writer.clone_document_from_reader(reader)
    for title, page_number in doc.canv.bookmarks:
        writer.add_outline_item(title, page_number)
    attachment=projection.parent/'breakeven_ledger.csv'
    writer.add_attachment('176信号_盈亏平衡逐笔明细.csv',attachment.read_bytes())
    writer.add_attachment('研究口径_study_config.json',(study/'study_config.json').read_bytes())
    writer.add_metadata({'/Title':'Astra 176张接管信号短期期权适配研究 - 盈亏平衡侵入口径','/Author':'Astra','/Subject':'176真实信号、双侧逐笔结果、补偿情景与初步效用判断；不代表实盘收益'})
    with output.open('wb') as stream:writer.write(stream)
    print(json.dumps({'pdf':str(output),'pages':len(reader.pages),'signals':len(ledger),'attached_ledger':attachment.name},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-dir',type=Path,required=True)
    parser.add_argument('--projection',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--scratch',type=Path,required=True)
    args=parser.parse_args();build(args.study_dir,args.projection,args.output,args.scratch)
