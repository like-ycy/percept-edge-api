import argparse
from collections import defaultdict
import glob
import json
import os
import sys
import time
from datetime import datetime
import os
import logging
import pandas as pd

from os.path import dirname

root_dir = dirname(dirname(dirname(os.path.realpath(__file__))))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from data.schema.dataset_statistics import *
from data.schema.dataset_config import DATASET_NAME_PATH_PAIRS


def send_report(info, report_address):

    # 自定义排序函数
    def custom_sort(col):
        if col.name == "dataset_name":
            # 定义特殊值的排序顺序
            special_order = {"realman", "cr1", "cr3-all", "cr3-v4"}
            # 如果str_column的值在特殊值中，返回对应的排序顺序；否则返回较大的值以确保这些值排在后面
            return ~col.isin(special_order)
        else:
            return -col

    # 创建DataFrame并转换为HTML
    summary = pd.DataFrame(info["summarys"]).T
    summary.index.name = "dataset_name"
    summary.reset_index(inplace=True)
    summary.sort_values(
        by=["dataset_name", "num_samples", "num_episodes"],
        key=custom_sort,
        inplace=True,
    )
    summary_html = summary.to_html(index=False, classes="table table-striped")

    total = summary.sum()
    total["num_datasets"] = len(summary)
    total = total.to_frame().T[["num_datasets", "num_samples", "num_episodes"]]
    total_html = total.to_html(index=False, classes="table table-striped")

    # 创建HTML报告
    report = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>数据统计报告</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 20px;
            }}
            h1 {{
                color: #333;
            }}
            .info {{
                margin-bottom: 20px;
            }}
            .table {{
                width: 100%;
                border-collapse: collapse;
            }}
            .table, .table th, .table td {{
                border: 1px solid #ddd;
            }}
            .table th, .table td {{
                padding: 8px;
                text-align: left;
            }}
            .table th {{
                background-color: #f2f2f2;
            }}
            .table-striped tbody tr:nth-of-type(odd) {{
                background-color: #f9f9f9;
            }}
        </style>
    </head>
    <body>
        <p>Hi, ALL</p>
        <br>
        <p>&nbsp;&nbsp;&nbsp;&nbsp;今日份数据统计报告如下:</p>
        <br>
        <div class="info">
            <p>&nbsp;&nbsp;&nbsp;&nbsp;- 版本: {info["version"]}</p>
            <p>&nbsp;&nbsp;&nbsp;&nbsp;- 路径: {info["file_path"]}</p>
            <p>&nbsp;&nbsp;&nbsp;&nbsp;- 耗时: {info["cost"]:.1f}s</p>
            <p>&nbsp;&nbsp;&nbsp;&nbsp;- 数据总量: </p>
            {total_html}
            <p>&nbsp;&nbsp;&nbsp;&nbsp;- 各数据集信息摘要:</p>
            {summary_html}
        </div>
    </body>
    </html>
    """

    attachments = [
        (info["file_path"], info["file_path"] + ".txt"),
        (info["save_xslx_file"]),
    ]

    from utils.mail import MailSender

    to_addrs = report_address
    subject = "数据统计信息更新"
    body = report
    mail_sender = MailSender()
    mail_sender.send_email(to_addrs, subject, body, attachments=attachments)


def get_dataset_info(dataset_name=None, max_episodes=None):
    """
    统一处理dataset_info的生成
    Args:
        dataset_name: 数据集名称列表，如果为None则使用所有数据集
        max_episodes: 每个数据集最大处理的episode数量
    Returns:
        dataset_info: 处理后的数据集信息列表
    """
    if dataset_name:
        dataset_info = [(n, DATASET_NAME_PATH_PAIRS[n]) for n in dataset_name]
    else:
        dataset_info = list(DATASET_NAME_PATH_PAIRS.items())

    # 统一处理dataset_info的格式
    dataset_info = [
        (i[0], [i[1]]) if isinstance(i[1], str) else i for i in dataset_info
    ]

    # 如果指定了max_episodes，则限制每个数据集的episode数量
    for i, (name, paths) in enumerate(dataset_info):
        files = []
        for path in paths:
            files.extend(glob.glob(os.path.join(path, "*.json")))

        # 如果指定了max_episodes，则限制每个数据集的episode数量
        if max_episodes is not None:
            files = files[:max_episodes]

        dataset_info[i] = (name, files)

    return dataset_info


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("--dataset_name", type=str, default=None, nargs="*")
    parser.add_argument("--save_dir", type=str, default=None)
    parser.add_argument("--max_episodes", type=int, default=None)
    parser.add_argument("--report_address", type=str, default=None, nargs="*")

    args = parser.parse_args()

    max_episodes = args.max_episodes
    dataset_name = args.dataset_name
    save_dir = args.save_dir or SAVE_DIR
    update_file = (
        os.path.join(save_dir, f"update.txt") if args.save_dir else UPDATE_FILE
    )
    report_address = args.report_address or [
        "maohui@ainnovation.com",
        "zhangfaen@ainnovation.com",
        "wangfei@ainnovation.com",
        "liuhaiying@ainnovation.com",
        "guojiangliang@ainnovation.com",
    ]

    from utils.logutil import setup_logger

    os.makedirs(save_dir, exist_ok=True)
    setup_logger(os.path.join(save_dir, f"log.txt"))

    version = datetime.now().strftime("%Y%m%d%H%M%S")
    logging.info(f"开始统计，version={version}")

    start = time.time()
    result = dict()

    def process_dataset(dataset_info):
        dataset_name, files = dataset_info
        logging.info(f"正在统计数据集：{dataset_name},共有{len(files)}个episode")
        return dataset_name, calculate_dataset_statistics_by_episode_files(
            dataset_name, files
        )

    from multiprocessing import Pool, Process, cpu_count

    try:
        dataset_info = get_dataset_info(dataset_name, max_episodes)
        save_xslx_file = os.path.join(save_dir, f"task_statistics_{version}.xlsx")

        profile_process = Process(
            target=data_profile_for_tasks,
            args=(
                {
                    k: v
                    for k, v in dataset_info
                    if k in ["cr1", "cr3-v4", "cr3-all"]
                },
                save_xslx_file,
            ),
            daemon=False,  # 可选：如果主进程退出，自动终止此进程
        )
        profile_process.start()

        with Pool(max(10, len(dataset_info))) as pool:
            result_list = pool.map(process_dataset, dataset_info)
        # 等待
        profile_process.join()
        # 结果是一个列表，其中每个元素都是一个(dataset_name, statistics)元组
        result = {name: stats for name, stats in result_list}

        cost = time.time() - start
        logging.info(f"完成一次统计，版本={version}, 用时{cost:.1f}s")

        summarys = defaultdict(dict)
        error = False
        for dataset_name, data in result.items():
            for info in data["choices"]:
                if "error" in info:
                    summarys[dataset_name].update({"error": info["error"]})
                    error = True
                else:
                    info["statistics"]["metadata"]["duration (hours)"] = round(
                        info["statistics"]["metadata"]["num_samples"] / (30 * 60 * 60),
                        2,
                    )
                    summarys[dataset_name].update(info["statistics"]["metadata"])

        os.makedirs(save_dir, exist_ok=True)
        if error:
            file_path = os.path.join(
                save_dir, f"dataset_statistics_{version}_error.json"
            )
            json.dump(result, open(file_path, "w"), ensure_ascii=False, indent=4)

        else:
            file_path = os.path.join(save_dir, f"dataset_statistics_{version}.json")
            json.dump(result, open(file_path, "w"), ensure_ascii=False, indent=4)
            with open(update_file, "w") as f:
                f.write(f"{file_path}")

        report_info = dict(
            version=version,
            file_path=file_path,
            save_xslx_file=save_xslx_file,
            cost=cost,
            summarys=summarys,
        )

        send_report(report_info, report_address=report_address)

    except Exception as e:
        logging.error(f"统计失败，错误信息：{e}, trace = {traceback.format_exc()}")
