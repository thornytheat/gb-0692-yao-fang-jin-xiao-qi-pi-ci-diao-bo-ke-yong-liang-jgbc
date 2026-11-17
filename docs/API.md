# 接口

`POST /api/run`：JSON中的csv文本，省略使用样例；或multipart字段file。最大5MB。

`POST /api/m1`至`POST /api/m7`：独立运行到指定模块，JSON结构同运行接口。

`GET /api/history`：历史批次。`POST /api/review`接收id、revision和decision（通过或退回）。`GET /api/evidence.csv`提供单一证据导出。

结构错误422，版本变化409，文件过大413；错误返回code与message。所有页面可从左侧七个模块入口访问。

`GET /api/runs/<id>`读取指定批次；页面与证据CSV通过run_id查询参数绑定批次，模块导航保留该参数。

`POST /api/recall`：方案复核通过后登记批号召回。JSON字段为run_id、sku、lot及可选receipts（数组，元素形如`{"需求单":"D0001","已收":10}`）。已收部分留存门店不动；未发部分退出源库可用量，并按现有FEFO与整包装口径改派其他合格批；原方案与收货记录随处置单保留。处置按调拨行逐点落库，中断后重复提交同一内容将从最后完成的处置点接续，不重复冻结或重派；同一方案同一批仅允许一份处置单，收货记录不一致返回409。方案未通过复核返回409（STATE），批号不在方案库存中返回422（RECALL）。

`GET /api/recall/<id>`读取处置单；`GET /api/recalls?run_id=`列出处方案下的处置单；`/recall/<id>`为召回复核页面，可核对原计划、已收、待执行与补偿量。本工具不接承运与签收系统，收货量以录入为准。

JSON请求体必须是对象，数组、标量、null、损坏JSON及错误上传字段返回400，且不运行默认样例。
