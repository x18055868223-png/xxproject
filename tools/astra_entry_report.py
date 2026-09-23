"""Produce a small research reading pack from completed sealed v1.3 results."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text("utf-8-sig"))


def build(quality_dir, structure_dir, ledger_dir, output):
    quality_dir, structure_dir, ledger_dir, output = map(Path, (quality_dir, structure_dir, ledger_dir, output))
    output.mkdir(parents=True, exist_ok=False)
    q, s, ledger = load(quality_dir / "summary.json"), load(structure_dir / "summary.json"), load(ledger_dir / "manifest.json")
    lines = ["# Astra v1.3：机会质量与同侧结构差额", "",
             "本轮执行两个独立命题。全部结果属于已研究历史的开发诊断；无同期真实信用，真实净胜率、EV和自然NR增益均未知。未替换运行模型、未推送部署、未启动90日窗口。", "",
             "## 机会质量：相同覆盖下的风险排序", "",
             "Put和Call分别比较。控制年度、实际宽度、期限、短腿距离及训练期波动层后，按冻结分数回顾性排序，主覆盖为50%。分位点使用评价期排序，只诊断分辨力，不是可上线择时阈值。", "",
             "| 侧别 | 方法 | 50%组平均归一赔付 | 未选组平均归一赔付 | 50%组ES95 | 有信息层权重 |", "|---|---|---:|---:|---:|---:|"]
    for side in ("put_credit", "call_credit"):
        for method in ("geometry", "statistical", "joint"):
            x = q["methods"][method]["by_side"][side]
            lines.append(f"| {side} | {method} | {x['conditional_mean_loss_selected']:.6f} | {x['conditional_mean_loss_deferred']:.6f} | {x['selected_es95_uncapped']:.6f} | {x['informative_weight_fraction']:.2%} |")
    lines += ["", "joint为原有机制特征模型的保存预测，不是本轮LLM效果。归一赔付的单位是原宽度按入场价折算的BTC价值，不能读作净收益或胜率。", "",
              "### 主差异及稳定性", ""]
    for side, result in q["statistical_vs_geometry_primary"].items():
        ci = result["bootstrap_difference_center_97_5_ci"]
        support = q["diagnostic_development_support"][side]
        failed = ", ".join(k for k, v in support["checks"].items() if not v) or "无"
        lines += [f"- {side}：统计减几何的平均赔付差为 `{result['mean_difference_stat_minus_geometry']:.8f}`；中心97.5%七日块区间 `{ci}`。",
                  f"  开发支持：`{support['support']}`；未通过：`{failed}`。",
                  f"  去掉10个对主均值差最有利的日期后：`{result['leave_10_most_favorable_out']['mean_difference_stat_minus_geometry']:.8f}`。",
                  f"  年度结果：`{json.dumps(result['annual'], ensure_ascii=False)}`。"]
    lines += ["", "### 入选日期等权的敏感性与放弃机会", "",
              "主评价保留原日期的机会权重；下表将每个有入选样本的日期重新等权，回答不同的问题。统计组在该敏感性下更差，说明原排序结果不能直接迁移为每日固定建仓量的规则。", "",
              "| 侧别 | 几何：入选日期等权赔付 | 统计：入选日期等权赔付 | 统计放弃组中零赔付占比 |", "|---|---:|---:|---:|"]
    for side in ("put_credit", "call_credit"):
        g = q["methods"]["geometry"]["by_side"][side]
        t = q["methods"]["statistical"]["by_side"][side]
        lines.append(f"| {side} | {g['selected_day_equal_mean_loss']:.6f} | {t['selected_day_equal_mean_loss']:.6f} | {t['deferred_zero_payout_fraction_of_deferred']:.2%} |")
    lines += ["", "零赔付不等于有足够信用、可成交或真实盈利；被放弃的无赔付机会已保留，不能只展示入选组表现。完整门槛、失效日期、暂缓机会与诊断支持状态见机会质量summary.json，所有失败均保留。", "",
              "## 同侧结构：有限侵入区的赔付节省", "",
              f"原结构记录{ledger['coverage']['input_rows']:,}条，精确同宽外移配对{ledger['coverage']['paired_rows']:,}条，缺口{ledger['coverage']['gap_rows']:,}条。年度开发评价{s['rows']:,}行、{s['days']:,}个交割日。", "",
              "两组均安全或均穿过保护腿时，差额为零；中间侵入区才节省赔付。正节省及外移组低赔付只是合约几何关系，开发支持必须来自预测误差、校准、年度稳定性和有效覆盖的共同增量。", "",
              "| 侧别 | 模型 | 差额MSE | 预测减实际的平均偏差 | 非回退权重 |", "|---|---|---:|---:|---:|"]
    for side, info in s["sides"].items():
        for model, x in info["models"].items():
            lines.append(f"| {side} | {model} | {x['mse']:.8f} | {x['bias']:+.6f} | {x['nonfallback_date_weight_fraction']:.2%} |")
    lines += ["", "| 侧别 | 比较 | MSE相对变化 | 四年非劣数 | 未满足门槛 |", "|---|---|---:|---:|---|"]
    for side, info in s["sides"].items():
        for comp in info["comparisons"]:
            base_mse = info["models"][comp["baseline"]]["mse"]
            failures = ", ".join(k for k, passed in comp["gates"].items() if not passed) or "无（仍仅开发支持）"
            n = sum(v <= 0 for v in comp["annual_mse_difference"].values())
            lines.append(f"| {side} | {comp['challenger']} vs {comp['baseline']} | {comp['mse_difference']/base_mse:+.3%} | {n}/4 | {failures} |")
    lines += ["", "### 尾损、覆盖与实际信用", ""]
    for side, info in s["sides"].items():
        p = info["full_payoff"]
        lines.append(f"- {side}：评价期配对覆盖{info['paired_fraction']:.2%}；原结构/外移结构ES95为{p['original_es95']:.6f}/{p['outward_es95']:.6f}，归一赔付超过1的行数为{p['original_over_one_rows']}/{p['outward_over_one_rows']}。")
    lines += ["", "2020年的配对覆盖明显低于后续年份；缺口由当时可用的精确同宽合约决定，不能当成全市场无偏样本。所有缺口及原/配对/缺口几何分布保留。", "",
              "每行预测只代表条件赔付节省的研究估计，不能据此放心少收同额信用；表的偏差、样本支持和不确定性均未被报价验证。真实信用、真实EV继续为空。", "",
              "## 可复现证据", "",
              f"- 机会质量：{quality_dir / 'summary.json'}", f"- 结构评价：{structure_dir / 'summary.json'}",
              f"- 全行独立赔付与合约核对：{structure_dir / 'independent_input_parity.json'}", f"- 结构配对与缺口：{ledger_dir / 'manifest.json'}",
              "- 协议、结果前补充、源hash、失败与本轮验收位于同一v1.3研究根目录。", "",
              "本轮没有新增模型/阈值搜索，未重新使用历史年份冒称未见检验；结果不授权修改FMZ或交易执行。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    plot(q, s, output / "research_results.png")


def plot(q, s, output):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt, font_manager
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), layout="constrained")
    colors = {"geometry": "#444444", "statistical": "#146b87", "joint": "#a16b32"}
    for ax, side, label in zip(axes[:2], ("put_credit", "call_credit"), ("Put", "Call")):
        for method in colors:
            curve = q["methods"][method]["coverage_curve"][side]
            keys = sorted(curve, key=float)
            ax.plot([100*float(k) for k in keys], [curve[k]["conditional_mean_loss_selected"] for k in keys],
                    marker="o", markersize=4, label=method, color=colors[method], linewidth=1.6)
        ax.set(title=f"{label}：同层、同覆盖的赔付风险", xlabel="回顾性覆盖率（%）", ylabel="平均归一赔付（越低越好）")
        ax.grid(axis="y", alpha=.18)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False, fontsize=8)
    ax = axes[2]
    labels = []
    for side, color in (("put", "#146b87"), ("call", "#a16b32")):
        for comparison, label in zip(s["sides"][side]["comparisons"], ("几何−历史均值", "几何波动−几何")):
            y = len(labels)
            labels.append(f"{side.title()} {label}")
            center = comparison["mse_difference"]
            low, high = comparison["interval"]["lower"], comparison["interval"]["upper"]
            ax.errorbar(center, y, xerr=[[center-low], [high-center]], fmt="o", color=color, capsize=3, markersize=5)
    ax.axvline(0, color="#777777", linewidth=.8)
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.invert_yaxis()
    ax.set(title="同侧差额：误差变化与区间", xlabel="MSE差：负值改善｜中心98.75%区间")
    ax.grid(axis="x", alpha=.18)
    ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Astra v1.3｜已研究历史的开发诊断；不代表可执行择时规则或净收益", fontsize=13)
    fig.savefig(output, dpi=160, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--quality", required=True)
    p.add_argument("--structure", required=True)
    p.add_argument("--ledger", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    build(args.quality, args.structure, args.ledger, args.output)
