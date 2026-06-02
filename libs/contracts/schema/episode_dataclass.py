import re
from typing import List, Optional, Union

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class CameraInfo(BaseModel):
    brand: str  # 相机品牌，比如 "Intel RealSense"
    model: str  # 相机型号，比如 "D435"
    detail: Optional[str] = None  # 这个型号相机的详细描述


class Metadata(BaseModel):
    """
    描述: 每个episode包含meta数据和steps数据, steps的每项数据为长度相等的数组.
    """

    dataset_name: Optional[
        str
    ]  # 数据集名称 | 取值范围: 自定义后固定 | 说明: 与目录结构中的${datasetname}一致
    episode_id: Optional[
        int
    ]  # 试验编号 | 取值范围: 0~2^31 | 说明: 每个episode对应1个完整的任务执行动作，与文件名中的${episodeID}一致
    experiment_time: Optional[str] = (
        None  # 试验时间 | 取值范围: 自定义后固定 | 说明: 每次试验开始时刻的日期和时分秒，与文件名中的${experimenttime}一致
    )
    operator: Optional[str] = (
        None  # 操作人员名字 ｜ 取值范围: 自定义 | 说明: 每次试验的操作人员
    )
    scene: Optional[str] = (
        None  # 试验场景 | 取值范围: 自定义后固定 | 说明: 描述试验场景，与文件名中的$(scene)一致
    )
    scene_description: Optional[str] = (
        None  # 试验场景描述 | 取值范围: 自定义后固定 | 说明: 描述试验场景的详细信息
    )
    environment: Optional[str] = (
        None  # 试验环境 ｜ 取值范围: 自定义后固定 | 说明: 描述机器人所在的具体环境，比如某个房间、某个厨房，与文件名中的${environment}一致
    )
    environment_description: Optional[str] = (
        None  # 试验环境描述 | 取值范围: 自定义后固定 | 说明: 描述试验环境的详细信息
    )
    task_name: Optional[str] = (
        None  # 任务名称（不是语言指令）| 取值范围: 自定义后固定 | 说明: 通常用于简单描述，与文件名中的${taskname}一致
    )
    lang_instructions: Optional[List[str]] = (
        None  # 可能的自然语言指令 | 取值范围: 自定义后固定 | 说明: 语言指令候选集合，同一个任务可能出现的不同表述方式, 存储为数组，自定义个数
    )
    goal_image: Optional[List[List[List[int]]]] = (
        None  # 目标图像 | 取值范围: 0~255 | 说明: 目标图像的像素数据，通常是完成任务之后的一帧RGB图像。shape=[H, W, C]
    )
    goal_depth: Optional[List[List[int]]] = (
        None  # 目标深度图像 | 取值范围: 0~2^31 | 说明: 目标深度图像的像素数据，通常是完成任务之后的一帧深度图像。shape=[H, W]
    )
    sample_rate: Optional[int] = (
        None  # 采样频率 ｜ 取值范围: 0~2^31 ｜ 说明: 每一秒钟采集多少个step，是相机帧率的采样率，所有其它数据通过上采样对齐到这个采样率
    )
    num_steps: int  # 总步数	 | 取值范围: 0~2^31 | 说明: 该任务包含的总steps
    robot_name: Optional[str] = (
        None  # 机器人名称 | 取值范围: 自定义后固定 | 说明: 与文件名中的${robotname}一致
    )
    robot_type: Optional[str] = (
        None  # 机器人类型 | 取值范围: ["single_arm","dual_arm"] | 说明: 后续如有需要，可增加类型，比如人形机器人、机器狗等
    )
    robot_description: Optional[str] = (
        None  # 描述机器人的arm1、arm2分别指哪个机械臂, camera1、camera2分别指哪个摄像头，采集的state和action分别代表的内容和组织形式等
    )
    robot_arm1_joints_state_dim: Optional[int] = (
        None  # arm1机械臂关节状态参数量 | 取值范围: 0~255 | 说明: arm1机械臂的状态参数数量，跟关节数量一致。
    )
    robot_arm2_joints_state_dim: Optional[int] = (
        None  # arm2机械臂关节状态参数量 | 取值范围: 0~255 | 说明: arm2机械臂的状态参数数量，跟关节数量一致
    )
    robot_arm1_eef_state_dim: Optional[int] = (
        None  # arm1机械臂姿态参数量 | 取值范围: 6 | 说明: arm1机械臂姿态参数数量，按顺序包含：x/y/z和rx/ry/rz
    )
    robot_arm2_eef_state_dim: Optional[int] = (
        None  # arm2机械臂姿态参数量 | 取值范围: 6 | 说明: arm2机械臂姿态参数数量，按顺序包含：x/y/z和rx/ry/rz
    )
    robot_arm1_gripper_state_dim: Optional[int] = (
        None  # arm1夹爪状态参数量 | 取值范围: 0~255 | 说明: arm1夹爪总的状态参数数量，按顺序包含：位移、速度、力。未来如果采用灵巧手，关节再定义
    )
    robot_arm2_gripper_state_dim: Optional[int] = (
        None  # arm2夹爪状态参数量 | 取值范围: 0~255 | 说明: arm2夹爪总的状态参数数量，按顺序包含：位移、速度、力。未来如果采用灵巧手，关节再定义
    )
    robot_arm1_sucker_state_dim: Optional[int] = (
        None  # 吸盘状态参数量 | 取值范围: -70~+70|[io,pressure],其中io为开关状态,数值为离散的0和1，0为关闭，1为开启，pressure为气压压强,单位为kPa, 范围为[-70, 0]
    )
    robot_arm2_sucker_state_dim: Optional[int] = (
        None  # 吸盘状态参数量 | 取值范围: -70~+70|[io,pressure],其中io为开关状态,数值为离散的0和1，0为关闭，1为开启，pressure为气压压强,单位为kPa, 范围为[-70, 0]
    )
    robot_torso_joints_state_dim: Optional[int] = (
        None  # 躯干关节状态参数量 | 取值范围: 0~255 | 说明: 躯干关节的状态参数数量，跟关节数量一致
    )
    robot_torso_eef_state_dim: Optional[int] = (
        None  # 躯干 eef 状态参数量 | 取值范围: 0~255 | 说明: 躯干 eef 的状态参数数量，跟 eef 数量一致
    )
    robot_hand1_joints_state_dim: Optional[int] = (
        None  # 手1关节状态参数量 | 取值范围: 0~255 | 说明: 手1关节的状态参数数量，跟关节数量一致
    )
    robot_hand2_joints_state_dim: Optional[int] = (
        None  # 手2关节状态参数量 | 取值范围: 0~255 | 说明: 手2关节的状态参数数量，跟关节数量一致
    )
    robot_hand1_force_state_dim: Optional[int] = (
        None  # 手1力状态参数量 | 取值范围: 0~255 | 说明: 手1力的状态参数数量，跟关节数量一致（）
    )
    robot_hand2_force_state_dim: Optional[int] = (
        None  # 手2力状态参数量 | 取值范围: 0~255 | 说明: 手2力的状态参数数量，跟关节数量一致（）
    )
    vr_hand1_eef_state_dim: Optional[int] = (
        None  # vr1 eef 状态参数量 | 取值范围: 0~255 | 说明: 手1 eef 的状态参数数量，跟 eef 数量一致
    )
    vr_hand2_eef_state_dim: Optional[int] = (
        None  # vr2 eef 状态参数量 | 取值范围: 0~255 | 说明: 手2 eef 的状态参数数量，跟 eef 数量一致
    )
    robot_head_joints_state_dim: Optional[int] = (
        None  # 头关节状态参数量 | 取值范围: 0~255 | 说明: 头关节的状态参数数量，跟关节数量一致
    )
    vr_head_eef_state_dim: Optional[int] = (
        None  # 头 eef 状态参数量 | 取值范围: 0~255 | 说明: 头 eef 的状态参数数量，跟 eef 数量一致
    )
    robot_master_arm1_joints_state_dim: Optional[int] = (
        None  # master_arm1机械臂关节状态参数量 | 取值范围: 0~255 | 说明: 示教主动臂arm1机械臂的状态参数数量，跟关节数量一致，有时可作为action使用
    )
    robot_master_arm2_joints_state_dim: Optional[int] = (
        None  # master_arm2机械臂关节状态参数量 | 取值范围: 0~255 | 说明: 示教主动臂arm2机械臂的状态参数数量，跟关节数量一致，有时可作为action使用
    )
    robot_master_arm1_eef_state_dim: Optional[int] = (
        None  # master_arm1机械臂姿态参数量 | 取值范围: 6 | 说明: 示教主动臂arm1机械臂姿态参数数量，按顺序包含：x/y/z和rx/ry/rz，有时可作为action使用
    )
    robot_master_arm2_eef_state_dim: Optional[int] = (
        None  # master_arm2机械臂姿态参数量 | 取值范围: 6 | 说明: 示教主动臂arm2机械臂姿态参数数量，按顺序包含：x/y/z和rx/ry/rz，有时可作为action使用
    )
    robot_master_arm1_gripper_state_dim: Optional[int] = (
        None  # master_arm1夹爪状态参数量 | 取值范围: 0~255 | 说明: 示教主动臂arm1夹爪总的状态参数数量，按顺序包含：位移、速度、力。未来如果采用灵巧手，关节再定义，有时可作为action使用
    )
    robot_master_arm2_gripper_state_dim: Optional[int] = (
        None  # master_arm2夹爪状态参数量 | 取值范围: 0~255 | 说明: 示教主动臂arm2夹爪总的状态参数数量，按顺序包含：位移、速度、力。未来如果采用灵巧手，关节再定义，有时可作为action使用
    )
    robot_lift_state_dim: Optional[int] = (
        None  # 升降状态参数量 | 取值范围: 0~255 | 说明: 升降机构的所有状态参数数量，按顺序包含：升降机构位置
    )
    robot_base_state_dim: Optional[int] = (
        None  # 底盘状态参数量 | 取值范围: 0~255 | 说明: 底盘的所有状态参数数量，按顺序包含：底盘x/y坐标值、底盘旋转角度
    )
    robot_arm1_joints_action_dim: Optional[int] = (
        None  # arm1机械臂关节控制参数量 | 取值范围: 0～255 | 说明: arm1机械臂的关节控制参数数量。按顺序包含：1～n关节的旋转角度。机械臂根部为1号关节点。
    )
    robot_arm2_joints_action_dim: Optional[int] = (
        None  # arm2机械臂关节控制参数量 | 取值范围: 0～255 | 说明: arm2机械臂的关节控制参数数量。按顺序包含：1～n关节的旋转角度。机械臂根部为1号关节点。
    )
    robot_arm1_eef_action_dim: Optional[int] = (
        None  # arm1机械臂姿态控制参数量 | 取值范围: 6 | 说明: arm1机械臂姿态的控制参数数量，按照顺序包含：在基准坐标系上的Δx/Δy/Δz/Δrx/Δry/Δrz
    )
    robot_arm2_eef_action_dim: Optional[int] = (
        None  # arm2机械臂姿态控制参数量 | 取值范围: 6 | 说明: arm2机械臂姿态的控制参数数量，按照顺序包含：在基准坐标系上的Δx/Δy/Δz/Δrx/Δry/Δrz
    )
    robot_arm1_gripper_action_dim: Optional[int] = (
        None  # arm1夹爪控制参数量 | 取值范围: 0~255 | 说明: arm1夹爪的控制参数数量，状态值0或者1
    )
    robot_arm2_gripper_action_dim: Optional[int] = (
        None  # arm2夹爪控制参数量 | 取值范围: 0~255 | 说明: arm2夹爪的控制参数数量，状态值0或者1
    )
    robot_arm1_sucker_action_dim: Optional[int] = (
        None  # arm1吸盘控制参数量 | 取值范围: -70~+70 | 说明: arm1吸盘的控制参数数量，状态值0或者1
    )
    robot_arm2_sucker_action_dim: Optional[int] = (
        None  # arm2吸盘控制参数量 | 取值范围: -70~+70 | 说明: arm2吸盘的控制参数数量，状态值0或者1
    )
    robot_torso_joints_action_dim: Optional[int] = (
        None  # 躯干关节控制参数量 | 取值范围: 0~255 | 说明: 躯干关节的控制参数数量，包含：1～n关节的旋转角度。机械臂根部为1号关节点
    )
    vr_hand1_eef_action_dim: Optional[int] = (
        None  # 手1机械臂姿态控制参数量 | 取值范围: 6 | 说明: 手1机械臂姿态的控制参数数量，按照顺序包含：在基准坐标系上的Δx/Δy/Δz/Δrx/Δry/Δrz（）
    )
    vr_hand2_eef_action_dim: Optional[int] = (
        None  # 手2机械臂姿态控制参数量 | 取值范围: 6 | 说明: 手2机械臂姿态的控制参数数量，按照顺序包含：在基准坐标系上的Δx/Δy/Δz/Δrx/Δry/Δrz（）
    )
    vr_head_eef_action_dim: Optional[int] = (
        None  # 头机械臂姿态控制参数量 | 取值范围: 6 | 说明: 头机械臂姿态的控制参数数量，按照顺序包含：在基准坐标系上的Δx/Δy/Δz/Δrx/Δry/Δrz（）
    )
    robot_lift_action_dim: Optional[int] = (
        None  # 升降机构控制参数量 | 取值范围: 0-255 | 说明: 升降机构的控制参数数量，包含：升降机构位移（Δh）
    )
    robot_base_action_dim: Optional[int] = (
        None  # 底盘控制参数量  | 取值范围: 0-255 | 说明: 底盘的控制参数数量，按顺序包含：底盘前进或后退位移（Δs）、底盘旋转角度（Δθ）
    )
    camera1_rgb_resolution: Optional[List[int]] = (
        None  # camera1相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera2_rgb_resolution: Optional[List[int]] = (
        None  # camera2相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera3_rgb_resolution: Optional[List[int]] = (
        None  # camera3相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera4_rgb_resolution: Optional[List[int]] = (
        None  # camera4相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera5_rgb_resolution: Optional[List[int]] = (
        None  # camera5相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera6_rgb_resolution: Optional[List[int]] = (
        None  # camera6相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera7_rgb_resolution: Optional[List[int]] = (
        None  # camera7相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera8_rgb_resolution: Optional[List[int]] = (
        None  # camera8相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera9_rgb_resolution: Optional[List[int]] = (
        None  # camera9相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera10_rgb_resolution: Optional[List[int]] = (
        None  # camera10相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera11_rgb_resolution: Optional[List[int]] = (
        None  # camera11相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera12_rgb_resolution: Optional[List[int]] = (
        None  # camera12相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera13_rgb_resolution: Optional[List[int]] = (
        None  # camera13相机RGB图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera1_depth_resolution: Optional[List[int]] = (
        None  # camera1相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera2_depth_resolution: Optional[List[int]] = (
        None  # camera2相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera3_depth_resolution: Optional[List[int]] = (
        None  # camera3相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera4_depth_resolution: Optional[List[int]] = (
        None  # camera4相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera5_depth_resolution: Optional[List[int]] = (
        None  # camera5相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera6_depth_resolution: Optional[List[int]] = (
        None  # camera6相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera7_depth_resolution: Optional[List[int]] = (
        None  # camera7相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera8_depth_resolution: Optional[List[int]] = (
        None  # camera8相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera9_depth_resolution: Optional[List[int]] = (
        None  # camera9相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera10_depth_resolution: Optional[List[int]] = (
        None  # camera10相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera11_depth_resolution: Optional[List[int]] = (
        None  # camera11相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera12_depth_resolution: Optional[List[int]] = (
        None  # camera12相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera13_depth_resolution: Optional[List[int]] = (
        None  # camera13相机depth图像分辨率 | 取值范围: 0～4096 | 说明: shape=[H, W]例如图像分辨率为4096×2160, 则可表示为[2160，4096], 数值可自行设定
    )
    camera1_depth_scale: Optional[float] = (
        None  # camera1相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera2_depth_scale: Optional[float] = (
        None  # camera2相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera3_depth_scale: Optional[float] = (
        None  # camera3相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera4_depth_scale: Optional[float] = (
        None  # camera4相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera5_depth_scale: Optional[float] = (
        None  # camera5相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera6_depth_scale: Optional[float] = (
        None  # camera6相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera7_depth_scale: Optional[float] = (
        None  # camera7相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera8_depth_scale: Optional[float] = (
        None  # camera8相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera9_depth_scale: Optional[float] = (
        None  # camera9相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera10_depth_scale: Optional[float] = (
        None  # camera10相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera11_depth_scale: Optional[float] = (
        None  # camera11相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera12_depth_scale: Optional[float] = (
        None  # camera12相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera13_depth_scale: Optional[float] = (
        None  # camera13相机depth的缩放比例 | 说明: 对原始数据进行缩放以保留有效范围的信息，不同的深度相机的有效范围不同，因此需要不同的缩放尺度。例如：D435相机的深度有效范围[700-3000]，单位mm，使用的缩放比例是1600，代表保留1.6m范围内的有效数据，超过1.6m范围的信息进行压缩
    )
    camera_naming_rule: Optional[str] = Field(
        default="camera[1-10]分别代表的相机为：1 - 左手腕部向上相机（主视角）, 2 - 右手腕部向上相机（主视角）, 3 - 前向腰部相机, 4 - 顶部相机, 5 - 头部相机, 6 - 其它(目前OpenX里不能归类到1-5的全部放到了6), 7 -支架顶部前向广角, 8-支架顶部后向广角, 9-左手腕部向下方向相机, 10-右手腕部向下方向相机, 11-头部双目左相机，12-头部双目右相机",
        frozen=True,
    )
    camera1_model_info: Optional[CameraInfo] = None  # camera1 相机型号信息
    camera2_model_info: Optional[CameraInfo] = None  # camera2 相机型号信息
    camera3_model_info: Optional[CameraInfo] = None  # camera3 相机型号信息
    camera4_model_info: Optional[CameraInfo] = None  # camera4 相机型号信息
    camera5_model_info: Optional[CameraInfo] = None  # camera5 相机型号信息
    camera6_model_info: Optional[CameraInfo] = None  # camera6 相机型号信息
    camera7_model_info: Optional[CameraInfo] = None  # camera7 相机型号信息
    camera8_model_info: Optional[CameraInfo] = None  # camera8 相机型号信息
    camera9_model_info: Optional[CameraInfo] = None  # camera9 相机型号信息
    camera10_model_info: Optional[CameraInfo] = None  # camera10 相机型号信息
    camera11_model_info: Optional[CameraInfo] = None  # camera11 相机型号信息
    camera12_model_info: Optional[CameraInfo] = None  # camera12 相机型号信息
    camera13_model_info: Optional[CameraInfo] = None  # camera13 相机型号信息
    camera_depth_convert_rule: Optional[str] = Field(
        default="将16bit深度图（数值单位mm）转成8bit灰度图时，使用的数值变换方法为：`y = tanh(x/camera_depth_scale)*255`, 其中x是16bit值，y为8bit值。 ",
        frozen=True,
    )
    # 这里展示一个代码示例，将16bit深度图（数值单位mm）转成8bit灰度图
    # ```
    # import numpy as np
    # frame_16bit = np.array([1000,2000],dtype=np.int16) # 16bit深度图
    # frame_8bit = ( np.tanh(frame_16bit/1600) * 255 ).astype(np.uint8)
    # ```


# class CameraFrame(BaseModel,model_config = ConfigDict(arbitrary_types_allowed=True)):
class CameraFrame(BaseModel):
    r"""all camera data in one step"""

    # 这段代码是用来禁止数据类型验证的
    model_config = ConfigDict(arbitrary_types_allowed=True)

    camera1_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera1相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera2_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera2相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera3_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera3相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera4_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera4相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera5_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera5相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera6_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera6相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera7_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera7相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera8_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera8相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera9_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera9相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera10_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera10相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera11_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera11相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera12_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera12相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera13_rgb: Optional[Union[np.ndarray, bytes]] = (
        None  # camera13相机RGB图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W,C], H-行数，W-列数，C-通道数(固定为3) ; 2.bytes: JPEG格式的图像字节流。
    )
    camera1_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera1相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera1_depth_scale)*255`;
    )
    camera2_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera2相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera2_depth_scale)*255`;
    )
    camera3_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera3相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera3_depth_scale)*255`;
    )
    camera4_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera4相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera4_depth_scale)*255`;
    )
    camera5_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera5相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera5_depth_scale)*255`;
    )
    camera6_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera6相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera6_depth_scale)*255`;
    )
    camera7_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera7相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera7_depth_scale)*255`;
    )
    camera8_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera8相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera8_depth_scale)*255`;
    )
    camera9_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera9相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera9_depth_scale)*255`;
    )
    camera10_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera10相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera10_depth_scale)*255`;
    )
    camera11_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera11相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera11_depth_scale)*255`;
    )
    camera12_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera12相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera12_depth_scale)*255`;
    )
    camera13_depth: Optional[Union[np.ndarray, bytes]] = (
        None  # camera13相机深度图像 | 取值范围 0~255 | 支持两种格式：1.np.ndarray: shape=[H,W], H-行数，W-列数; 2.bytes: JPEG格式的图像字节流。 | 从16bit到8bit的转换方式  `y=tanh(x/camera13_depth_scale)*255`;
    )


class Observation(BaseModel):
    r"""all observation in a step"""

    arm1_joints_state: Optional[List[float]] = (
        None  # arm1机械臂关节状态 | 取值范围: -2pi~2pi | 说明: shape=[arm1_joints_state_dim]。数据组织顺序：从机械臂根部到终端依次各关节点，单位为弧度。
    )
    arm2_joints_state: Optional[List[float]] = (
        None  # arm2机械臂关节状态 | 取值范围: -2pi~2pi | 说明: shape=[arm2_joints_state_dim]。数据组织顺序：从机械臂根部到终端依次各关节点，单位为弧度。
    )
    arm1_eef_state: Optional[List[float]] = (
        None  # arm1机械臂姿态 | 取值范围: 自定义 | 说明: shape=[6]。数据组织顺序: x/y/z/rx/ry/rz。xyz单位为米，rx/ry/rz单位为弧度。
    )
    arm2_eef_state: Optional[List[float]] = (
        None  # arm2机械臂姿态 | 取值范围: 自定义 | 说明: shape=[6]。数据组织顺序: x/y/z/rx/ry/rz。xyz单位为米，rx/ry/rz单位为弧度。
    )
    arm1_gripper_state: Optional[List[float]] = (
        None  # arm1夹爪状态 | 取值范围: 自定义 | 说明: shape=[arm1_gripper_state_dim]。数据组织顺序: 夹爪的位移、速度、力。位移单位为米，速度单位为米/秒，力单位为牛。未来如果采用灵巧手，再定义
    )
    arm2_gripper_state: Optional[List[float]] = (
        None  # arm2夹爪状态 | 取值范围: 自定义 | 说明: shape=[arm2_gripper_state_dim]。数据组织顺序: 夹爪的位移、速度、力。位移单位为米，速度单位为米/秒，力单位为牛。未来如果采用灵巧手，再定义
    )
    arm1_sucker_state: Optional[List[float]] = (
        None  # arm1吸盘状态 | 取值范围: 自定义 | 说明: shape=[arm1_sucker_state_dim]。数据组织顺序: 吸盘的开关状态、气压。开关状态单位为无，气压单位为米/秒。未来如果采用灵巧手，再定义
    )
    arm2_sucker_state: Optional[List[float]] = (
        None  # arm2吸盘状态 | 取值范围: 自定义 | 说明: shape=[arm2_sucker_state_dim]。数据组织顺序: 吸盘的开关状态、气压。开关状态单位为无，气压单位为米/秒。未来如果采用灵巧手，再定义
    )
    master_arm1_joints_state: Optional[List[float]] = (
        None  # master_arm1机械臂关节状态 | 取值范围: -2pi~2pi | 说明: shape=[master_arm1_joints_state_dim]。数据组织顺序：从机械臂根部到终端依次各关节点，单位为弧度。
    )
    master_arm2_joints_state: Optional[List[float]] = (
        None  # master_arm2机械臂关节状态 | 取值范围: -2pi~2pi | 说明: shape=[master_arm2_joints_state_dim]。数据组织顺序：从机械臂根部到终端依次各关节点，单位为弧度。
    )
    master_arm1_eef_state: Optional[List[float]] = (
        None  # master_arm1机械臂姿态 | 取值范围: 自定义 | 说明: shape=[6]。数据组织顺序: x/y/z/rx/ry/rz。xyz单位为米，rx/ry/rz单位为弧度。
    )
    master_arm2_eef_state: Optional[List[float]] = (
        None  # master_arm2机械臂姿态 | 取值范围: 自定义 | 说明: shape=[6]。数据组织顺序: x/y/z/rx/ry/rz。xyz单位为米，rx/ry/rz单位为弧度。
    )
    master_arm1_gripper_state: Optional[List[float]] = (
        None  # master_arm1夹爪状态 | 取值范围: 自定义 | 说明: shape=[master_arm1_gripper_state_dim]。数据组织顺序: 夹爪的位移、速度、力。位移单位为米，速度单位为米/秒，力单位为牛。未来如果采用灵巧手，再定义
    )
    master_arm2_gripper_state: Optional[List[float]] = (
        None  # master_arm2夹爪状态 | 取值范围: 自定义 | 说明: shape=[master_arm2_gripper_state_dim]。数据组织顺序: 夹爪的位移、速度、力。位移单位为米，速度单位为米/秒，力单位为牛。未来如果采用灵巧手，再定义
    )
    lift_state: Optional[List[float]] = (
        None  # 升降机构状态参数 | 取值范围: 0~1 | 说明: shape=[1]。数据组织顺序: 升降机构位置，表示的是当前位置在整个可调整范围内的相对位置， 即 (当前高度-最小高度)/(最大高度-最小高度)。
    )
    base_state: Optional[List[float]] = (
        None  # 底盘姿态参数 | 取值范围: 自定义 | 说明: shape=[3]。数据组织顺序: 底盘位置坐标值（x/y）、底盘旋转角度（θ）。xy单位为米，θ单位为弧度。
    )
    translation_state: Optional[List[float]] = None  # 平移数据
    torso_joints_state: Optional[List[float]] = None  # 躯干姿态
    torso_eef_state: Optional[List[float]] = None  # 躯干 eef
    hand1_joints_state: Optional[List[float]] = None  # 手1关节状态
    hand2_joints_state: Optional[List[float]] = None  # 手2关节状态
    hand1_force_state: Optional[List[float]] = None  # 手1关节力
    hand2_force_state: Optional[List[float]] = None  # 手2关节力
    vr_hand_left_eef_state: Optional[List[float]] = None  # 手1 eef
    vr_hand_right_eef_state:  Optional[List[float]] = None  # 手2 eef
    head_joints_state: Optional[List[float]] = None  # 头关节状态
    vr_head_eef_state: Optional[List[float]] = None  # 头 eef
    adsorption_state: Optional[List[float]] = None  # 磁吸状态, 0/1代表吸和不吸
    camera_frame: Optional[CameraFrame] = Field(
        default=None, exclude=True
    )  # | 说明: 这个字段不会序列化到json
    timestamp: Optional[int] = (
        None  # 记录毫秒级时间戳 | 说明：使用 int(time.time() * 1000)
    )


class Action(BaseModel):
    r"""all action in a step"""

    # 相对位姿 + 绝对位姿
    arm1_joints_action_delta: Optional[List[float]] = (
        None  # arm1机械臂关节动作数据, 相对位姿 | 取值范围: -2pi~2pi | 说明: shape=[arm1_joints_action_dim]。数据组织顺序: 1～n节点的旋转角度，单位为弧度。机械臂根部为1号关节点。
    )
    arm1_joints_action_abs: Optional[List[float]] = (
        None  # arm1机械臂关节动作数据, 绝对位姿 | 取值范围: -2pi~2pi | 说明: shape=[arm1_joints_action_dim]。数据组织顺序: 1～n节点的旋转角度，单位为弧度。机械臂根部为1号关节点。
    )

    arm2_joints_action_delta: Optional[List[float]] = (
        None  # arm2机械臂关节动作数据, 相对位姿 | 取值范围: -2pi~2pi | 说明: shape=[arm2_joints_action_dim]。数据组织顺序: 1～n节点的旋转角度，单位为弧度。机械臂根部为1号关节点。
    )
    arm2_joints_action_abs: Optional[List[float]] = (
        None  # arm2机械臂关节动作数据, 绝对位姿 | 取值范围: -2pi~2pi | 说明: shape=[arm2_joints_action_dim]。数据组织顺序: 1～n节点的旋转角度，单位为弧度。机械臂根部为1号关节点。
    )

    arm1_eef_action_delta: Optional[List[float]] = (
        None  # arm1机械臂姿态动作数据, 相对位姿 | 取值范围: 自定义，-2pi~2pi | 说明: shape=[6]。数据组织顺序: Δx/Δy/Δz/Δrx/Δry/Δrz。Δx/Δy/Δz的单位为米，Δrx/Δry/Δrz的单位为弧度。
    )
    arm1_eef_action_abs: Optional[List[float]] = (
        None  # arm1机械臂姿态动作数据, 绝对位姿  | 取值范围: 自定义，-2pi~2pi | 说明: shape=[6]。数据组织顺序: x/y/z/rx/ry/rz。x/y/z的单位为米，rx/ry/rz的单位为弧度。
    )

    arm2_eef_action_delta: Optional[List[float]] = (
        None  # arm2机械臂姿态动作数据, 相对位姿 | 取值范围: 自定义，-2pi~2pi | 说明: shape=[6]。数据组织顺序: Δx/Δy/Δz/Δrx/Δry/Δrz。Δx/Δy/Δz的单位为米，Δrx/Δry/Δrz的单位为弧度。
    )
    arm2_eef_action_abs: Optional[List[float]] = (
        None  # arm2机械臂姿态动作数据, 绝对位姿  | 取值范围: 自定义，-2pi~2pi | 说明: shape=[6]。数据组织顺序: x/y/z/rx/ry/rz。x/y/z的单位为米，rx/ry/rz的单位为弧度。
    )

    lift_action_abs: Optional[List[float]] = (
        None  # 升降机构动作数据, 绝对位姿 | 取值范围: 自定义，0~1 | 说明: shape=[1]。数据组织顺序: 升降机构绝对位置, 0~1, 表示的是目标位置在整个可调整范围内的相对位置， 即 (目标高度-最小高度)/(最大高度-最小高度)。
    )
    lift_action_delta: Optional[List[float]] = (
        None  # 升降机构动作数据, 相对位姿 | 取值范围: 自定义，-1~1 | 说明: shape=[1]。数据组织顺序: 升降机构相对位移,s 相当于lift_action_abs-lift_state。
    )

    # 只有绝对位姿的单元
    arm1_gripper_action_abs: Optional[List[float]] = (
        None  # arm1夹爪动作数据, 绝对位姿 | 取值范围: 0~1 | 说明: shape=[1]。0代表关闭，1代表张开。连续值表示张开的比例。
    )
    arm2_gripper_action_abs: Optional[List[float]] = (
        None  # arm2夹爪动作数据, 绝对位姿 | 取值范围: 0~1 | 说明: shape=[1]。0代表关闭，1代表张开。连续值表示张开的比例。
    )
    arm1_sucker_action_abs: Optional[List[float]] = (
        None  # arm1吸盘动作数据, 绝对位姿 | 取值范围: 0~1 | 说明: shape=[1]。0代表关闭，1代表张开。连续值表示气压的比例。
    )
    arm2_sucker_action_abs: Optional[List[float]] = (
        None  # arm2吸盘动作数据, 绝对位姿 | 取值范围: 0~1 | 说明: shape=[1]。0代表关闭，1代表张开。连续值表示气压的比例。
    )

    # 只有相对位姿的单元
    base_action_delta: Optional[List[float]] = (
        None  # 底盘动作数据, 相对位姿| 取值范围: 自定义，-2pi~2pi | 说明: shape=[2]。数据组织顺序: 底盘前进或后退位移（Δs）、底盘旋转角度（Δθ），Δs的单位为米，Δθ的单位为弧度。
    )

    torso_joints_action_delta: Optional[List[float]] = None  # 躯干动作数据, 相对位姿
    torso_joints_action_abs: Optional[List[float]] = None  # 躯干动作数据, 绝对位姿
    torso_eef_action_delta: Optional[List[float]] = None  # 躯干eef动作数据, 相对位姿
    torso_eef_action_abs: Optional[List[float]] = None  # 躯干eef动作数据, 绝对位姿
    hand1_joints_action_delta: Optional[List[float]] = None  # 手1关节动作数据, 相对位姿
    hand1_joints_action_abs: Optional[List[float]] = None  # 手1关节动作数据, 绝对位姿
    hand2_joints_action_delta: Optional[List[float]] = None  # 手2关节动作数据, 相对位姿
    hand2_joints_action_abs: Optional[List[float]] = None  # 手2关节动作数据, 绝对位姿
    vr_hand1_eef_action_delta: Optional[List[float]] = None  # 手1 eef 数据, 相对位姿
    vr_hand1_eef_action_abs: Optional[List[float]] = None  # 手1 eef 数据, 绝对位姿
    vr_hand2_eef_action_delta: Optional[List[float]] = None  # 手2 eef 数据, 相对位姿
    vr_hand2_eef_action_abs: Optional[List[float]] = None  # 手2 eef 数据, 绝对位姿
    head_joints_action_delta: Optional[List[float]] = None  # 头关节动作数据, 相对位姿
    head_joints_action_abs: Optional[List[float]] = None  # 头关节动作数据, 绝对位姿
    vr_head_eef_action_delta: Optional[List[float]] = None  # 头 eef 数据, 相对位姿
    vr_head_eef_action_abs: Optional[List[float]] = None  # 头 eef 数据, 绝对位姿

    is_terminal: Optional[bool] = (
        None  # 试验是否结束帧 | 取值范围: [True, False] | 说明: 如果所有的step中都不包含is_terminal为True的情况，说明当前episode并不完整
    )


class Step(BaseModel):
    r"""a step"""

    observation: Observation = None
    action: Action = None


class Episode(BaseModel):
    r"""an episode or a demo"""

    metadata: Metadata
    steps: List[Step]

    def _get_camera_keys(metadata, pattern):
        """Helper to extract camera keys matching a pattern"""
        keys = []
        for attr_name, attr_value in metadata.__dict__.items():
            if re.match(pattern, attr_name) and attr_value is not None:
                keys.append(attr_name.rsplit("_", 1)[0])
        return keys
