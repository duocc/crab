#!/usr/bin/env python3
import requests
import argparse
import sys
import re
import io
from datetime import datetime
import os
import concurrent.futures # For multithreading
import time # For basic timing

# (VALID_CONTENT_TYPES and IGNORED_CONTENT_TYPES_PATTERNS remain the same)
VALID_CONTENT_TYPES = [
    "application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl",
    "video/mp2t", "video/mpeg", "video/x-msvideo", "video/mp4",
    "audio/mpeg", "audio/aac", "audio/ogg", "application/octet-stream",
    "application/x-mpegURL", "video/x-flv",
]

IGNORED_CONTENT_TYPES_PATTERNS = [
    r"text/html", r"application/json", r"text/plain", r"image/.*",
    r"application/pdf", r"application/xml", r"text/xml",
]

def is_ignored_content_type(content_type):
    if not content_type: return False
    content_type = content_type.lower().split(';')[0].strip()
    for pattern in IGNORED_CONTENT_TYPES_PATTERNS:
        if re.match(pattern, content_type, re.IGNORECASE): return True
    return False

def parse_m3u_content_with_extinf_str(content_lines_str):
    parsed_entries = []
    current_extinf = None
    for line_str in content_lines_str:
        line = line_str.strip()
        if not line: continue
        if line.startswith("#EXTINF:"):
            current_extinf = line
        elif line.startswith("#"):
            pass
        else:
            parsed_entries.append({'extinf': current_extinf, 'url': line, 'original_line_number': len(parsed_entries) +1}) # Add original order
            current_extinf = None
    if not parsed_entries: print(f"警告: 未从M3U内容中解析到任何流条目。")
    return parsed_entries

def fetch_m3u_from_url(m3u_url, timeout=30):
    print(f"正在从 URL 下载 M3U 文件: {m3u_url}")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 ... M3U-Checker/1.0'}
        response = requests.get(m3u_url, headers=headers, timeout=timeout, stream=True)
        response.raise_for_status()
        m3u_text_content = response.content
        try: return m3u_text_content.decode('utf-8').splitlines()
        except UnicodeDecodeError:
            print("UTF-8解码失败，尝试latin-1...")
            return m3u_text_content.decode('latin-1').splitlines()
    except Exception as e:
        print(f"错误: 下载 M3U 文件失败 ({type(e).__name__}): {e}")
        sys.exit(1)


def read_m3u_from_file(file_path):
    print(f"正在从本地文件读取 M3U: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f: return f.read().splitlines()
    except UnicodeDecodeError:
        print(f"UTF-8解码文件 {file_path} 失败, 尝试 latin-1...")
        with open(file_path, 'r', encoding='latin-1') as f: return f.read().splitlines()
    except Exception as e:
        print(f"错误: 读取 M3U 文件失败 ({type(e).__name__}): {e}")
        sys.exit(1)

def check_url_worker(entry_tuple):
    """
    Worker function for ThreadPoolExecutor.
    Accepts a tuple: (entry_dict, timeout_seconds)
    Returns a tuple: (entry_dict, is_valid, message)
    """
    entry, timeout = entry_tuple
    url = entry['url']
    # print(f"线程检查: {url[:50]}...") # Optional: for verbose thread activity logging

    headers = {'User-Agent': 'Mozilla/5.0 ... M3U-Checker/1.0'} # Ensure User-Agent
    try:
        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True, stream=False)
        status_code = response.status_code
        content_type_header = response.headers.get('Content-Type', '')
        content_type_cleaned = content_type_header.lower().split(';')[0].strip()

        if status_code == 200:
            if any(valid_ct == content_type_cleaned for valid_ct in VALID_CONTENT_TYPES):
                return entry, True, f"有效 (状态: {status_code}, 类型: {content_type_header})"
            elif is_ignored_content_type(content_type_cleaned):
                return entry, False, f"无效 (状态: {status_code}, 类型: {content_type_header} - 通常不是流媒体)"
            else:
                # print(f"警告: URL {url} 状态 200 OK，但 Content-Type '{content_type_header}' 未在已知有效列表中。")
                return entry, True, f"可能有效 (状态: {status_code}, 类型: {content_type_header} - 未知)"
        elif 300 <= status_code < 400 :
            return entry, False, f"无效 (重定向问题: {status_code}, 位置: {response.headers.get('Location', 'N/A')})"
        elif 400 <= status_code < 600:
            return entry, False, f"无效 (HTTP 错误: {status_code})"
        else:
            return entry, False, f"无效 (未知状态: {status_code}, 类型: {content_type_header})"
    except requests.exceptions.Timeout:
        return entry, False, "无效 (连接超时)"
    except requests.exceptions.TooManyRedirects:
        return entry, False, "无效 (重定向过多)"
    except requests.exceptions.ConnectionError as e: # More specific
        return entry, False, f"无效 (连接错误: {str(e)})"
    except requests.exceptions.RequestException as e:
        return entry, False, f"无效 (请求错误: {type(e).__name__} - {str(e)})"
    except Exception as e: # Catch-all for unexpected errors within the thread
        return entry, False, f"无效 (检查URL时发生线程内未知错误: {type(e).__name__} - {str(e)})"

def save_valid_m3u(valid_entries, output_dir, base_filename_prefix="valid_"):
    if not valid_entries:
        print("没有有效的链接可以保存。")
        return None
    today_date = datetime.now().strftime("%Y-%m-%d")
    output_filename = f"{base_filename_prefix}{today_date}.m3u"
    if output_dir and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print(f"创建输出目录: {output_dir}")
        except OSError as e:
            print(f"创建输出目录失败 {output_dir}: {e}, 回退到当前目录.")
            output_dir = "."
    output_filepath = os.path.join(output_dir if output_dir else ".", output_filename)
    print(f"\n正在将有效链接保存到: {output_filepath}")
    try:
        # Sort valid_entries by original line number to maintain order as much as possible
        valid_entries_sorted = sorted(valid_entries, key=lambda x: x.get('original_line_number', float('inf')))
        with open(output_filepath, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for entry in valid_entries_sorted:
                if entry['extinf']: f.write(f"{entry['extinf']}\n")
                f.write(f"{entry['url']}\n")
        print(f"成功保存 {len(valid_entries)} 个有效条目到 {output_filepath}")
        return output_filepath
    except IOError as e:
        print(f"保存文件 {output_filepath} 失败: {e}")
        return None


if __name__ == "__main__":
    start_time = time.time()
    parser = argparse.ArgumentParser(description="多线程检查 M3U 链接并保存有效链接。")
    parser.add_argument("m3u_source", help="M3U 文件的 URL 或本地路径。")
    parser.add_argument("--timeout", type=int, default=10, help="每个流链接的请求超时时间 (秒)。")
    parser.add_argument("--download-timeout", type=int, default=45, help="下载M3U文件本身的超时时间 (秒)。")
    parser.add_argument("--output-dir", type=str, default=".", help="保存有效 M3U 文件的目录。")
    parser.add_argument("--output-prefix", type=str, default="valid_zho_", help="生成的有效M3U文件名的前缀。")
    parser.add_argument("--workers", type=int, default=10, help="用于检查 URL 的并发工作线程数。") # Max workers

    args = parser.parse_args()
    
    print(f"Python script m3u_checker.py started execution! Max workers: {args.workers}", flush=True)

    m3u_content_lines_str = []
    if args.m3u_source.startswith(('http://', 'https://')):
        m3u_content_lines_str = fetch_m3u_from_url(args.m3u_source, timeout=args.download_timeout)
    else:
        m3u_content_lines_str = read_m3u_from_file(args.m3u_source)

    if not m3u_content_lines_str:
        print("未能获取或读取 M3U 内容。")
        sys.exit(1)
    
    all_m3u_entries = parse_m3u_content_with_extinf_str(m3u_content_lines_str)

    if not all_m3u_entries:
        print("M3U 内容中没有找到可检查的条目。")
        sys.exit(0)

    valid_entries_to_save = []
    invalid_links_count = 0
    summary_log = [] # To store log messages for final summary
    
    # Prepare tasks for ThreadPoolExecutor
    # We only check http/https links
    tasks_to_run = []
    skipped_non_http_count = 0
    for entry in all_m3u_entries:
        if entry['url'].startswith(('http://', 'https://')):
            tasks_to_run.append((entry, args.timeout)) # Pack entry and timeout
        else:
            skipped_non_http_count += 1
            summary_log.append(f"[~] {entry['extinf'] if entry['extinf'] else entry['url']} | {entry['url']} - 非HTTP/HTTPS链接，跳过检查")


    print(f"\n准备检查 {len(tasks_to_run)} 个 HTTP/HTTPS M3U 条目 (跳过 {skipped_non_http_count} 个非HTTP/HTTPS条目) 使用最多 {args.workers} 个线程...\n")
    
    # Using ThreadPoolExecutor for concurrent checks
    # Results will come in whatever order they complete
    results_from_threads = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        # Submit tasks
        future_to_entry = {executor.submit(check_url_worker, task): task[0] for task in tasks_to_run} # task[0] is the entry
        
        processed_count = 0
        for future in concurrent.futures.as_completed(future_to_entry):
            original_entry = future_to_entry[future]
            processed_count += 1
            try:
                _entry, is_valid, message = future.result() # _entry is the same as original_entry
                
                # Print progress immediately
                progress_percent = (processed_count / len(tasks_to_run)) * 100 if tasks_to_run else 0
                print(f"[{processed_count}/{len(tasks_to_run)} - {progress_percent:.1f}%] URL: {_entry['url'][:60]}... -> {message}")

                log_message_prefix = _entry['extinf'] if _entry['extinf'] else _entry['url']
                if is_valid:
                    valid_entries_to_save.append(_entry)
                    summary_log.append(f"[✓] {log_message_prefix} | {_entry['url']} - {message}")
                else:
                    invalid_links_count += 1
                    summary_log.append(f"[✗] {log_message_prefix} | {_entry['url']} - {message}")
            except Exception as exc:
                invalid_links_count += 1
                # This catch is if future.result() itself throws an error NOT caught inside check_url_worker
                # (though check_url_worker is designed to catch its own exceptions)
                err_msg = f"线程执行任务时发生主控错误 for {original_entry['url']}: {exc}"
                print(err_msg)
                summary_log.append(f"[✗] {original_entry['extinf'] if original_entry['extinf'] else original_entry['url']} | {original_entry['url']} - {err_msg}")
    
    # Sort summary log by original line number for readability
    # We need original_line_number in the entry dictionary for this to work well.
    # Let's assume 'original_line_number' was added during parsing.
    # If not, sorting summary_log might not be by original order.
    # For now, summary_log items are appended as threads complete.
    # To sort summary log, we'd need to store the original index with each log entry.
    # Alternatively, just print valid/invalid counts and then the detailed log.

    print("\n--- 检查结果摘要 (条目顺序可能与原始文件不同，取决于线程完成顺序) ---")
    # Sort summary_log. To do this properly, each item in summary_log should store original_line_number
    # For simplicity now, let's sort the valid_entries_to_save before writing to file.
    # The summary_log will be printed as results came in.
    for item in summary_log:
        print(item)
    
    print(f"\n总共解析 M3U 条目数: {len(all_m3u_entries)}")
    print(f"实际检查HTTP/HTTPS链接数: {len(tasks_to_run)}")
    print(f"跳过非HTTP/HTTPS链接数: {skipped_non_http_count}")
    print(f"有效条目数 (将被保存): {len(valid_entries_to_save)}")
    print(f"无效链接数: {invalid_links_count}")

    saved_filepath = None
    if valid_entries_to_save:
        # Sorting happens inside save_valid_m3u now
        saved_filepath = save_valid_m3u(valid_entries_to_save, args.output_dir, args.output_prefix)
        if saved_filepath:
            # print(f"::set-output name=saved_m3u_path::{saved_filepath}")
            # --- 修改开始 ---
            # 旧方法: print(f"::set-output name=saved_m3u_path::{saved_filepath}")
            # 新方法: 写入到 GITHUB_OUTPUT 文件
            github_output_file = os.getenv('GITHUB_OUTPUT')
            if github_output_file:
                with open(github_output_file, 'a') as f: # Append mode
                    f.write(f"saved_m3u_path={saved_filepath}\n")
                print(f"已将输出 saved_m3u_path={saved_filepath} 写入 GITHUB_OUTPUT")
            else:
                print(f"警告: GITHUB_OUTPUT 环境变量未设置。无法设置Action输出。")
            # --- 修改结束 ---

    end_time = time.time()
    print(f"\n总耗时: {end_time - start_time:.2f} 秒.")

    if invalid_links_count > 0 and len(tasks_to_run) > 0 :
        print("\n检测到无效链接。")
        sys.exit(1)
    elif len(tasks_to_run) == 0 and len(all_m3u_entries) > 0:
        print("\n没有可检查的HTTP/HTTPS链接，但M3U中存在条目。")
        sys.exit(0)
    else:
        print("\n所有可检查的链接均有效或可能有效。")
        sys.exit(0)
