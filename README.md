# 地方志编目本地查档工具

一个纯本地、零外部依赖（Python 3.10+ 标准库 + SQLite）的命令行查档工具，
解决街巷/行政区改名、撤并、划转背景下的历史地址归属问题。

## 数据与规则约定

| 约定 | 实现 |
| --- | --- |
| 街巷、行政区改名后编号沿用 | 名称按 `street_id` / `district_id` 挂多条有效期记录 |
| 名称按原字匹配 | 不做模糊/简繁匹配，只取落款当天有效名称精确相等的记录 |
| 日期区间 | 含首日、不含末日 `[start, end)` |
| 门牌范围 | 两端都含的整数闭区间 |
| 划转资料编号 | 街巷编号、门牌已统一到同一套旧号，导入即直接使用 |

### 归属判定规则

1. 落款当天从**当日地址归属**（membership）查起；录入了区名/门牌就加约束，
   没有区名保留所有同名候选，没有门牌保留整条门牌范围。
2. 查不到原地址单独标注原因（街名/区名/门牌/归属缺失），不参与划转推断。
3. 落款日之后的划转在**生效当天**就算数；同一天的划转都按**当天变更前**的
   归属一起算（快照语义）。
4. 一条候选在前一次划转后只剩部分门牌可行，后面的划转只能继续作用于这部分；
   当天新划入某区的门牌不会被同区旧划转回头切割。
5. 原区仍存在时，没划走的地址留在原区；原区撤销且有地址没有去向记录时，
   标出这部分**断在哪一天、哪个区**。
6. 指定查询日期须在落款当天或之后；落款当天的划转在当天查询时不应用。

### 目录修订与引用

- 编目员可保存并启用更正后的**完整目录**（新 `revision_id`）；新查询默认用
  最新启用修订，也可用 `--revision` 指定旧修订。
- 一份修订若把**同一天、同一旧区、同一条街的同一门牌**划给两个不同区，
  启用被拒绝，继续使用上一份可用目录（拒绝记录留痕、明细不入库）。
  划给同一个区的重复划转不视为冲突。
- 查询加 `--cite` 生成可重开引用编号（`REF000001`…），保存当时的档案原文、
  目录修订、查询日期、结果和全部依据；之后改目录不影响旧引用。
- 档案、修订、引用全部落在本地 SQLite，关掉工具再打开仍可查。

## 快速开始

隔离模式运行（推荐，不加载当前目录下的任何第三方模块）：

```bash
python3 -I gaz.py import-catalog demo/catalog-rev-1985.json
python3 -I gaz.py import-archives demo/archives.json
```

数据库默认在 `~/.gazetteer/gazetteer.db`，可用 `--db` 或环境变量
`GAZETTEER_DB` 指定其他位置。

## 命令一览

```bash
# 目录与档案
python3 -I gaz.py import-catalog <目录.json>      # 校验+保存+启用（冲突则拒绝）
python3 -I gaz.py import-archives <档案.json>     # 批量导入/更新（同编号覆盖）
python3 -I gaz.py list revisions|archives|citations
python3 -I gaz.py use <revision_id>               # 启用一份旧修订

# 按档案查（--on 默认今天；--revision 默认最新启用；--cite 生成引用）
python3 -I gaz.py query A001 --on 2005-01-01 --cite
python3 -I gaz.py query A003 --on 2020-01-01 --revision rev-1985
python3 -I gaz.py query A001 --json               # 机器可读输出

# 从区反查某天可能归属它的档案（区编号或当日有效区名）
python3 -I gaz.py reverse D2 --on 2005-01-01
python3 -I gaz.py reverse 城中区 --on 2005-01-01

# 重开历史引用
python3 -I gaz.py reopen REF000001
```

## JSON 数据格式

### 目录

```json
{
  "revision_id": "rev-1985",
  "note": "说明文字",
  "streets": [
    {"id": "S1", "name": "中山路", "valid_from": "1949-01-01", "valid_to": null}
  ],
  "districts": [
    {"id": "D1", "name": "第一区",
     "established_on": "1949-01-01", "abolished_on": "1985-06-01",
     "names": [
       {"name": "第一区", "valid_from": "1949-01-01", "valid_to": "1985-06-01"}
     ]}
  ],
  "memberships": [
    {"id": "M1", "street_id": "S1", "district_id": "D1",
     "lo": 1, "hi": 99, "valid_from": "1949-01-01", "valid_to": null}
  ],
  "transfers": [
    {"id": "T1", "effective_on": "1985-06-01", "street_id": "S1",
     "lo": 1, "hi": 50, "from_district_id": "D1", "to_district_id": "D2"}
  ]
}
```

`valid_to` / `abolished_on` 为 `null` 表示至今有效；末日为开区间端点（不含）。
撤销区在撤销当天的"变更前归属"用一段 `valid_to` 为次日的 membership 表达，
划转后的静态归属段从划转次日生效，两者按半开区间自然衔接。

### 档案

```json
[
  {"archive_id": "A001", "signing_date": "1980-03-01",
   "address_text": "中山路十号第一区公所函",
   "street_name": "中山路", "district_name": "第一区", "house_number": 10}
]
```

`district_name`、`house_number` 可省略，分别对应"没录区名/没录门牌"。

## 测试

```bash
python3 -I -m unittest discover -s tests -v
```

## 代码结构

```
gazetteer/
  intervals.py   门牌闭区间运算（挖差、求交、不合并的片段保留）
  dates.py       ISO 日期与 [start, end) 半开区间判定
  models.py      街名/区名/归属/划转/目录的领域模型
  engine.py      名称解析、同日快照划转模拟、正查、区反查
  validation.py  启用前冲突校验
  storage.py     SQLite 持久化（档案/修订/引用）
  importer.py    JSON 导入与结构校验
  rendering.py   中文文本输出
  cli.py         命令行入口
demo/            覆盖改名、同名歧义、撤并断档、部分划转、冲突拒绝的样例数据
tests/           规则测试（37 项）
```
