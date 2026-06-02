import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Any

import ijson
from dataset_config import DATASET_NAME_PATH_PAIRS
from utils.mail import MailSender

COLLECT_DATA_DIR = "/mnt/nas03/robot_collect_data"


class DataStatistics:
    """采集数据统计"""

    STEPS_PER_HOUR = 1800

    HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>数据统计报告</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1, h2 {{ color: #333; }}
            .info {{ margin-bottom: 20px; }}
            .table {{ width: 100%; border-collapse: collapse; }}
            .table, .table th, .table td {{ border: 1px solid #ddd; }}
            .table th, .table td {{ padding: 8px; text-align: left; }}
            .table th {{ background-color: #f2f2f2; }}
            .table-striped tbody tr:nth-of-type(odd) {{ background-color: #f9f9f9; }}
            .summary-row {{ font-weight: bold; background-color: #e6f3ff; }}
            .section {{ margin-bottom: 40px; }}
        </style>
    </head>
    <body>
        {content}
    </body>
    </html>
    """

    def __init__(self):
        self.operator_map = {
            "wx": "望翔",
            "lb": "李博",
            "cqy": "陈秋雨",
            "gmj": "顾梦静",
            "jmt": "金满堂",
            "wzf": "王中峰",
            "cz": "常振",
        }

        self.robot_name_map = {
            "cr1": "CR-1",
            "cr3": "CR-3",
            "cr4a": "CR-4A",
            "cr4b": "CR-4B",
            "cr4a_env": "CR-4A",
            "cr4b_env": "CR-4B",
            "cobotmagic": "CR-1",
            "cobot_magic": "CR-1",
        }

    def get_operator_name(self, operator_code: str) -> str:
        """获取采集员名称"""
        if operator_code in self.operator_map:
            return self.operator_map[operator_code]

        if "_" in operator_code:
            names = [
                self.operator_map.get(code, code) for code in operator_code.split("_")
            ]
            return "_".join(filter(None, names))

        return operator_code

    def get_robot_name(self, robot_code: str) -> str:
        """获取机器人名称"""
        return self.robot_name_map.get(robot_code.lower(), robot_code)

    def parse_json_metadata(self, json_path: str) -> Dict[str, Any]:
        """解析JSON文件"""
        try:
            with open(json_path, "rb") as f:
                metadata = next(ijson.items(f, "metadata"), None)
                if metadata:
                    return {
                        "operator": metadata.get("operator"),
                        "num_steps": metadata.get("num_steps", 0),
                        "robot_name": self.get_robot_name(
                            metadata.get("robot_name", "未知")
                        ),
                        "task_name": metadata.get("task_name", "未知"),
                    }

                # 采集数据
                f.seek(0)
                metadata = next(ijson.items(f, "metadata_ex.standard_metadata"), {})
                return {
                    "operator": metadata.get("operator"),
                    "num_steps": metadata.get("num_steps", 0),
                    "robot_name": self.get_robot_name(
                        metadata.get("robot_name", "未知")
                    ),
                    "task_name": metadata.get("task_name", "未知"),
                }
        except Exception as e:
            logging.error(f"处理文件 {json_path} 时出错: {e}")
            return {}

    def scan_json_files(
        self, folder_paths: List[str], target_date: str = None
    ) -> List[Dict]:
        """提取统计信息"""
        stats_list = []

        for folder_path in self._ensure_list(folder_paths):
            if not os.path.exists(folder_path):
                logging.warning(f"目录不存在: {folder_path}")
                continue

            for json_file in self._find_json_files(folder_path, target_date):
                metadata = self.parse_json_metadata(json_file)
                if metadata and metadata.get("operator"):
                    stats_list.append(self._create_stat_record(metadata, json_file))

        return stats_list

    def _ensure_list(self, paths) -> List[str]:
        return [paths] if isinstance(paths, str) else paths

    def _find_json_files(self, base_path: str, target_date: str = None) -> List[str]:
        """查找JSON文件"""
        json_files = []

        # 单个JSON文件
        if os.path.isfile(base_path) and base_path.endswith(".json"):
            if not target_date or self._is_target_date_file(base_path, target_date):
                return [base_path]
            return []

        # 目录查找
        for root, dirs, files in os.walk(base_path):
            if target_date and not self._is_target_date_dir(root, target_date):
                continue

            for file in files:
                if file.endswith(".json"):
                    json_files.append(os.path.join(root, file))

        return json_files

    def _is_target_date_dir(self, dir_path: str, target_date: str) -> bool:
        dir_name = os.path.basename(dir_path)
        return target_date in dir_name

    def _is_target_date_file(self, file_path: str, target_date: str) -> bool:
        if not target_date:
            return True

        parent_dir = os.path.dirname(file_path)
        return self._is_target_date_dir(parent_dir, target_date)

    def _create_stat_record(self, metadata: Dict, file_path: str) -> Dict:
        return {
            "robot_name": metadata["robot_name"],
            "operator": metadata["operator"],
            "name_cn": self.get_operator_name(metadata["operator"]),
            "operation_time": metadata["num_steps"] / self.STEPS_PER_HOUR,
            "task_name": metadata["task_name"],
            "count": 1,
            "file_path": file_path,
        }

    def generate_total_statistics_html(self, folder_paths: List[str]) -> str:
        """生成总量统计HTML"""
        stats_list = self.scan_json_files(folder_paths)

        if not stats_list:
            return self.HTML_TEMPLATE.format(
                content="<h2>总量统计</h2><p>没有找到数据</p>"
            )

        summary = self._aggregate_total_stats(stats_list)

        rows = []
        total_time = total_count = 0

        for robot_name in sorted(summary.keys()):
            stats = summary[robot_name]
            robot_time = stats["total_time"] / 60

            total_time += robot_time
            total_count += stats["count"]

            rows.append(
                f"""
            <tr>
                <td>{robot_name}</td>
                <td>{robot_time:.2f}</td>
                <td>{stats['count']}</td>
            </tr>
            """
            )

        content = f"""
        <div class="section">
            <h2>总量统计</h2>
            <table class="table table-striped">
                <tr><th>机器人名称</th><th>有效时间（小时）</th><th>采集条数</th></tr>
                {''.join(rows)}
                <tr class="summary-row">
                    <td><strong>汇总</strong></td>
                    <td><strong>{total_time:.2f}</strong></td>
                    <td><strong>{total_count}</strong></td>
                </tr>
            </table>
        </div>
        """

        return self.HTML_TEMPLATE.format(content=content)

    def _aggregate_total_stats(self, stats_list: List[Dict]) -> Dict:
        """聚合总量统计数据"""
        summary = defaultdict(lambda: {"total_time": 0, "count": 0})

        for stat in stats_list:
            robot_name = stat["robot_name"]
            summary[robot_name]["total_time"] += stat["operation_time"]
            summary[robot_name]["count"] += stat["count"]

        return summary

    def generate_daily_statistics_html(self, folder_paths: List[str]) -> str:
        """生成当日统计HTML"""
        today = datetime.now().strftime("%Y%m%d")
        stats_list = self.scan_json_files(folder_paths, today)

        if not stats_list:
            content = f"""
            <div class="section">
                <h2>采集员当日采集统计 - {today}</h2>
                <p>没有找到当日数据</p>
            </div>
            """
            return self.HTML_TEMPLATE.format(content=content)

        summary = self._aggregate_daily_stats(stats_list)

        rows = []
        total_time = total_count = 0

        for key in sorted(summary.keys()):
            stats = summary[key]
            task_names = "<br>".join(stats["task_names"])

            total_time += stats["total_time"]
            total_count += stats["count"]

            rows.append(
                f"""
            <tr>
                <td>{stats['robot_name']}</td>
                <td>{stats['name_cn']}</td>
                <td>{stats['total_time']:.2f}</td>
                <td>{task_names}</td>
                <td>{stats['count']}</td>
            </tr>
            """
            )

        content = f"""
        <div class="section">
            <h2>采集员当日采集统计 - {today}</h2>
            <p>共有 {len(stats_list)} 条数据</p>
            <table class="table table-striped">
                <tr>
                    <th>机器人名称</th><th>采集员</th><th>有效时间（分钟）</th>
                    <th>任务名称</th><th>采集条数</th>
                </tr>
                {''.join(rows)}
                <tr class="summary-row">
                    <td><strong>汇总</strong></td><td><strong>-</strong></td>
                    <td><strong>{total_time:.2f}</strong></td><td><strong>-</strong></td>
                    <td><strong>{total_count}</strong></td>
                </tr>
            </table>
        </div>
        """

        return self.HTML_TEMPLATE.format(content=content)

    def _aggregate_daily_stats(self, stats_list: List[Dict]) -> Dict:
        """聚合当日统计数据"""
        summary = {}

        for stat in stats_list:
            key = (stat["robot_name"], stat["operator"])
            if key not in summary:
                summary[key] = {
                    "robot_name": stat["robot_name"],
                    "name_cn": stat["name_cn"],
                    "total_time": 0,
                    "task_names": set(),
                    "count": 0,
                }

            summary[key]["total_time"] += stat["operation_time"]
            summary[key]["task_names"].add(stat["task_name"])
            summary[key]["count"] += stat["count"]

        return summary

    def generate_report(
        self, daily_paths: List[str], all_paths: List[List[str]]
    ) -> str:
        """生成完整报告"""
        all_flat_paths = []
        for sublist in all_paths:
            if isinstance(sublist, list):
                all_flat_paths.extend(sublist)
            else:
                all_flat_paths.append(sublist)

        total_html = self.generate_total_statistics_html(all_flat_paths)
        daily_html = self.generate_daily_statistics_html(daily_paths)

        total_body = self._extract_body_content(total_html)
        daily_body = self._extract_body_content(daily_html)

        return self.HTML_TEMPLATE.format(content=total_body + daily_body)

    def _extract_body_content(self, html: str) -> str:
        start = html.find("<body>") + 6
        end = html.find("</body>")
        return html[start:end].strip() if start > 6 and end > start else html

    def send_report(self, daily_paths: List[str], all_paths: List[List[str]]):
        """发送统计报告"""
        report_html = self.generate_report(daily_paths, all_paths)

        mail_sender = MailSender()
        mail_sender.send_email(
            to_addrs=[
                "maohui@ainnovation.com",
                "guojiangliang@ainnovation.com",
                "wangzhongfeng@ainnovation.com",
                "v-wangxiang@ainnovation.com",
                "zhangfaen@ainnovation.com",
            ],
            subject="采集数据统计信息",
            body=report_html,
        )


def main():
    statistics = DataStatistics()
    statistics.send_report(
        daily_paths=[COLLECT_DATA_DIR],
        all_paths=[
            DATASET_NAME_PATH_PAIRS["cr1"],
            DATASET_NAME_PATH_PAIRS["cr4a"],
            DATASET_NAME_PATH_PAIRS["cr4b"],
        ],
    )


if __name__ == "__main__":
    main()
