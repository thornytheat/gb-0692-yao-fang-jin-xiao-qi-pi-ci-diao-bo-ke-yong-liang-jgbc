# 深度登记 药房近效期批次调拨与可用量

- 规模：中型
- 使用者与工作流：连锁药房库存核对员，汇集批号库存与调拨申请，按可用有效期安排门店补货。
- 核心规则：FEFO仅分配到店后剩余有效期满足需求的库存；冻结量先扣，拆包需整包装，召回批不得调拨。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 库存接收 | 运行库存接收并复核证据 | `POST /api/m1`；`/work/1` | `app/modules/m1_receiving/rules.py` | `tests/test_m1.py` |
| M2 批号裁决 | 运行批号裁决并复核证据 | `POST /api/m2`；`/work/2` | `app/modules/m2_lots/rules.py` | `tests/test_m2.py` |
| M3 效期分层 | 运行效期分层并复核证据 | `POST /api/m3`；`/work/3` | `app/modules/m3_expiry/rules.py` | `tests/test_m3.py` |
| M4 需求净额 | 运行需求净额并复核证据 | `POST /api/m4`；`/work/4` | `app/modules/m4_demand/rules.py` | `tests/test_m4.py` |
| M5 调拨分配 | 运行调拨分配并复核证据 | `POST /api/m5`；`/work/5` | `app/modules/m5_allocation/rules.py` | `tests/test_m5.py` |
| M6 收发对账 | 运行收发对账并复核证据 | `POST /api/m6`；`/work/6` | `app/modules/m6_reconcile/rules.py` | `tests/test_m6.py` |
| M7 方案复核 | 运行方案复核并复核证据 | `POST /api/m7`；`/work/7` | `app/modules/m7_approval/rules.py` | `tests/test_m7.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | BOM | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m1_receiving/rules.py` | `tests/test_m1.py::test_bom` |
| TRAP-05 | 列数不齐 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m1_receiving/rules.py` | `tests/test_m1.py::test_bad_columns` |
| TRAP-07 | 空值数量 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_lots/rules.py` | `tests/test_m2.py::test_missing_quantity` |
| TRAP-08 | 药品编码前导零 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_lots/rules.py` | `tests/test_m2.py::test_sku_leading_zero` |
| TRAP-14 | 同单冲突 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_lots/rules.py` | `tests/test_m2.py::test_conflict_not_override` |
| TRAP-19 | 召回优先 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m2_lots/rules.py` | `tests/test_m2.py::test_recall_dominates` |
| TRAP-23 | 效期端点 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m3_expiry/rules.py` | `tests/test_m3.py::test_expired_isolated` |
| TRAP-26 | 陈旧复核 | 静默覆盖或误计 | 显式裁决并保留证据 | `app/modules/m7_approval/rules.py` | `tests/test_m7.py::test_review_api_stale` |

## 对账规则

输入记录 = 采信记录 + 隔离记录 + 相同行重复。重复表头与空行单独记数。业务合计按阶段中的守恒或差值列复核。运行清单有原文SHA256、规则版本与耗时；相同规则版本下同文重复提交复用批次。

## 召回处置

方案复核通过后可登记批号召回（`POST /api/recall`，入口在模块七页面，复核页`/recall/<id>`）。已收部分留存门店不动；未发部分退出源库可用量，并复用FEFO、整包装与到店余期口径改派其他合格批，未分配余量一并退出。处置单保留原方案行与收货记录，复核页可核对原计划、已收、待执行与补偿量，守恒关系为：原计划=已收+待执行，待执行=补偿+补偿缺口。处置按调拨行逐点落库（recalls表），中断后续跑从最后完成的处置点接续；同一内容重复提交复用原处置单，不重复冻结或重派；同一方案同一批仅一份处置单，收货记录不一致返回409。后续召回的补偿池自动排除已登记召回批及其已消耗补偿。本工具不接承运与签收系统，收货量以录入为准。

## 已知不覆盖

部署为本地单用户工具，不连接外部上游系统。CSV单文件5MB及一万条业务记录上限；不推断未提供的未知数据。
