# 采集后数据的上传、存储、转换和版本控制规则

1. 数据的三个存储位置：
* 机器人环境端侧设备，用于进行采集过程中数据的实时存储；
* nas03磁盘robot_mixed_raw_data文件夹，用于储存raw data；
* nas03磁盘ainno_robot_datasets文件夹，用于存储宪法数据。

2. 采集后数据的上传、存储、转换流程：
* 采集的数据首先保存在机器人环境端侧设备上，定时或按需将端侧设备上的数据上传至nas03磁盘robot_mixed_raw_data文件夹，并及时清除端侧设备上已经确认上传过的数据文件；
* 在raw data完成阶段性累积后或者根据具体训练需要，在ainno_robot_datasets文件夹下相应位置创建新的临时文件夹，将robot_mixed_raw_data文件夹中新增的raw data转换成宪法数据，并与已有全部相关宪法数据合并存储至新的临时文件夹，待存储完成后立即将该临时文件夹改名为以当天日期为后缀名的新的版本文件夹（如cobot_magic_20241218），形成新版本宪法数据，同时更新dataloader.py中相应数据文件夹地址并提交MR。

3. 采集后数据的上传、存储和版本控制要求：
* 数据从采集后到存储为宪法数据，仅允许在1.中明确的三个位置存储，不允许在其他任何地方存储；
* 各版本数据遵循各自的格式和内容要求，不允许以任何其他版本形式存在；
* raw data含有全部宪法数据的全量原始信息；
* 新版本宪法数据一旦创建形成，不允许做任何更改和增删；
* 下一版本宪法数据包括上一版本的全部内容，并增加新的数据（特殊情况可删除前一版本中的问题数据）；
* 模型训练只允许使用宪法数据，不允许使用其他任何版本数据，即训练所需的数据必须提前转化存储为宪法数据。#


# 宪法数据目录结构与文件内容规则

创新奇智自建机器人数据集，主要包含三部分内容：自采单臂机器人数据、自采双臂机器人数据、开源数据。数据按照如下组织规范管理：

一、目录结构
```bash
AInnoRobotDatasets
	|
	| single_arm
	|	| ${datasetname}
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera1_rgb.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera2_rgb.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera3_rgb.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera4_rgb.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera5_rgb.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera1_depth.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera2_depth.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera3_depth.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera4_depth.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}_camera5_depth.mp4
	|	|	| ${experimenttime}_${datasetname}_${robotname}_${scene}_${environment}_${taskname}_${episodeID}.json
	|	| ${datasetname}
	|
	| dual_arm
	|	| ${datasetname}
	|	|	| xxxx_rgb.mp4
	|	|	| xxxx_depth.mp4
	|	|	| xxxx.json
	|
	| third_party
	|	| ${datasetname}
	|	|	| xxxx_rgb.mp4
	|	|	| xxxx_depth.mp4
	|	|	| xxxx.json



说明：
${experimenttime}-----试验时间
${datasetname}--------数据子集名称
${robotname}----------机器人名称
${scene}--------------试验场景
${environment}--------试验环境
${taskname}-----------任务名称
${episodeID}----------试验编号
xxx_rgb.mp4-----------包含相机对齐之后的RGB视频帧，帧数等于step的数量
xxx_depth.mp4---------包含相机对齐之后的深度数据帧，帧数等于step的数量
xxx.json--------------包含meta和steps的格式化数据文件
```

二、文件内容

参考episode_dataclass.py

