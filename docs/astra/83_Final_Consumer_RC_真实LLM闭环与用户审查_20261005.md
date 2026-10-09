# xxproject Final Consumer RC Closure Receipt｜2026-10-05

真实 LLM 的严格消费链已修复。最终封版资格以本轮 `RC_FREEZE.json` 的独立复核与包验证记录为准；当前工作停在用户主观审查入口，不做推送部署。

| 项目 | 回执 |
|---|---|
| Baseline RC | WPEM-CONSUMER-RC-20261005-02，原始字节与旧失败保留 |
| New source identity | `WPEM-CONSUMER-2.5.2-1cf9ecea036f`，由实际源码版本图生成 |
| Provider / model | DeepSeek，request `deepseek-v4-flash` / response `deepseek-flash` |
| Prompt / config | `wpem_bounded_prompt@1.0.1` / `wpem_json_delivery@1.0.1` |
| Review input / output | `review_input@2.5.2` / `bounded_claim_ids@2.5.0` |
| WPEM evidence | `wpem_review_evidence@1.0.1`，完整事实投影及覆盖保留 |
| Actual HTTP calls | 本轮 5/5 次严格通过，0 次拒绝，0 次重试；前三次在文案修正前的源身份，最后两次绑定最终源身份 |
| Final-identity independent successes | risk_null 与真实冻结资料，两个独立 provider response ID，连续通过 |
| Real / risk_null | 都有本轮真实成功回执；合法模拟仍是 synthetic，真实资料仍是 frozen replay |
| Strict validator / parser | 未放宽，旧 extra type / 双对象 / 越目录 ID 仍拒绝；原始响应未修补 |
| Risk / mu / M | REFERENCE_BLOCKED；mu/M null；成功 LLM 不取得风险资格 |
| Financial semantics | 无变化；21 个组件字节不变，严格金融校验函数 AST 不变，7 份冻结快照内容相同 |
| Frontend | 桌面 1440×900、390×844 已验证；冻结研究成功不进入 CURRENT；覆盖与绑定可展开 |
| Clean regression | 366 passed / 0 failed / 0 errors / 0 skipped；source drift=0 |
| Canonical manifest SHA | `724d5bed4a0f95c7ca43715ce84036d198726472d252278830d025456cca25c6` |
| Package / independent review | 最终 hash 与复核结果写入 `RC_FREEZE.json` / `PACKAGE_VALIDATION.json`，不制造循环身份 |
| Production / runtime / FMZ / account | 未部署、未推送；没有修改服务器代码、服务、LLM timer、FMZ 或账户动作 |
| User review | [简单审查入口](http://127.0.0.1:8913/user-review) · [真实资料](http://127.0.0.1:8913/?sample=real_native#candidatesTitle) · [路径增量模拟](http://127.0.0.1:8913/?sample=risk_null#candidatesTitle) |

旧三次失败经过原 parser/validator 回放后，确认两次将 HTTP 的 json_object 配置写入了 message.content，另一次混用了内嵌来源目录 ID。修复只为请求提供明确的单对象、九字段 JSON 样例及顶层目录白名单，声明来源目录不可选；交付模式关闭 thinking。没有删掉 WPEM 事实、放宽允许 ID 或新增自由金融文本。输入版本变化记录请求合同版本，使旧输入不能冒充新投影；2.5.1 与旧证据的2.5.0仍可按原形回放。

当前 Chat Completions transport 没有经过实测的项目原生 strict schema 支持，因此沿用 JSON object 并以原严格本地校验验收。[官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)与[JSON Output 格式样例要求](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)只是传输参考，五次真实响应才是本轮通过证据。

同版证据见 `ACTUAL_WPEM_LLM_FRONTEND_LINEAGE.json/.md`：底层原始来源与 snapshot → WPEM Level/Delta/Path/coverage → Review Evidence → 实际 HTTP request body → provider 原始 HTTP bytes → 单对象 parser → 既有严格 validator → 合法 bounded IDs → 程序派生 Q1/Q2 → public reader。最终请求内的 manifest hash 与 canonical manifest 字节完全一致。

最终 manifest 的不可变源身份先在私有目录生成；真实验证成功后才将同字节提升为 canonical。封版资格、provider 响应和包 hash 单独记录，避免把验收结果反写进请求身份后借用旧响应。源码或协议变更后的候选从不借用旧版成功。

合法夹具还覆盖同终点不同路径、位置改善但补偿变薄、责任恶化但补偿变厚、换腿、来源/方法断点、risk_null；CATALOG_GAP、STALE、rejected、CURRENT/RESEARCH_OK 的边界由本版测试验证。夹具不会伪装为 actual provider response。旧三次失败和首个文案修正前的三个实际成功完整保留。

必要的前端修正仅将被动回放的“零真实 LLM”改为“查看不调用 LLM”，并将评审来源交给现有回执标識，避免否认已保存的研究成功。下拉导航溢出来自本地验收服务重复附加成功说明，缩短的是验收服务导航标签；没有修改已验证的前端组件、样本金融数据或源身份。

保留限制：真实原生 same-candidate 15 分钟窗口尚未合格；risk reference BLOCKED、mu/M null 是正确边界；没有经济 Alpha、账户交易结果或完整 FMZ/runtime 生产 E2E 的证明。unknown label 密度、长审计可读性、展开状态、间距与命名留给用户主观反馈，当前不强行打磨。

回滚入口：`.artifacts/final_consumer_rc_closure_20261005_02/rollback_local.py`。默认仅校验；`--apply` 在逐项核对 before/after hash 与路径后恢复本轮之前的源字节，新测试文件移入本轮可恢复目录。原始证据、服务器密钥、budget、状态、FMZ JSONL、服务与 timer 不回滚。执行前应先退出本地验收服务，并只恢复已审查的源码；没有在本轮实际执行回滚。

两个 explorer 完成失败归因与版本链检查；worker 实现了独立范围的输入兼容测试及被动来源文案；fresh reviewer 独立核对拒绝边界、源身份、原始 HTTP、页面与包。主 agent 对 raw response、源码不变性、最终请求绑定、干净源测试和 ZIP 字节逐项读回。

唯一下一步：用户看页面，回答入口中的六个主观问题。反馈只归入 UX_COPY、VISUAL_HIERARCHY、INFORMATION_ORDER、AUDIT_DISCOVERABILITY、SEMANTIC_BUG；前四类做最小 polish，最后一类才重开相应模块。反馈闭环后再进入推送部署，不提前发布。
