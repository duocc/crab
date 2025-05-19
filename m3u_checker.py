#!/usr/bin/env python3
import requests
import argparse
import sys
import re
import io # 用于处理内存中的文本流

# 常见的流媒体 Content-Type
VALID_CONTENT_TYPES = [
    "application/vnd.apple.mpegurl",  # HLS M3U8 manifest
    "application/x-mpegurl",          # Alternative M3U8
    "audio/mpegurl",                  # M3U for audio
    "video/mp2t",                     # MPEG2-TS (Transport Stream)
    "video/mpeg",
    "video/x-msvideo",                # AVI
    "video/mp4",
    "audio/mpeg",                     # MP3
    "audio/aac",
    "audio/ogg",
    "application/octet-stream",       # 有时直播流会用这个，需要谨慎判断
    "application/x-mpegURL",          # 注意大小写
    "video/x-flv",                    # FLV
]

# 忽略的 Content-Type，这些通常不是有效的流
IGNORED_CONTENT_TYPES_PATTERNS = [
    r"text/html",
    r"application/json",
    r"text/plain",
    r"image/.*", # 忽略所有图片类型
    r"application/pdf",
    r"application/xml",
    r"text/xml",
]

def is_ignored_content_type(content_type):
    if not content_type:
        return False
    content_type = content_type.lower().split(';')[0].strip()
    for pattern in IGNORED_CONTENT_TYPES_PATTERNS:
        if re.match(pattern, content_type, re.IGNORECASE):
            return True
    return False

def parse_m3u_content(content_lines):
    """从给定的文本行列表解析 M3U 内容并提取 URL"""
    urls = []
    for line_bytes in content_lines: # content_lines are bytes if from response.iter_lines()
        try:
            line = line_bytes.decode('utf-8').strip()
        except UnicodeDecodeError:
            # Try another common encoding or skip if problematic
            try:
                line = line_bytes.decode('latin-1').strip()
            except UnicodeDecodeError:
                print(f"警告: 跳过无法解码的行: {line_bytes[:50]}...")
                continue

        if line and not line.startswith("#"):
            urls.append(line)
    if not urls:
        print(f"警告: 未从M3U内容中解析到任何流 URL。")
    return urls

def fetch_m3u_from_url(m3u_url, timeout=30):
    """从 URL 下载 M3U 内容，返回行列表"""
    print(f"正在从 URL 下载 M3U 文件: {m3u_url}")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 M3U-Checker/1.0'
        }
        # 使用 stream=True 和 iter_lines() 处理可能较大的文件并逐行解码
        response = requests.get(m3u_url, headers=headers, timeout=timeout, stream=True)
        response.raise_for_status() # 如果下载失败则抛出 HTTPError
        return list(response.iter_lines()) # 返回字节行列表
    except requests.exceptions.Timeout:
        print(f"错误: 下载 M3U 文件超时: {m3u_url}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"错误: 下载 M3U 文件失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"下载或初步处理 M3U 内容时出错: {e}")
        sys.exit(1)

def read_m3u_from_file(file_path):
    """从本地文件读取 M3U 内容，返回行列表"""
    print(f"正在从本地文件读取 M3U: {file_path}")
    try:
        with open(file_path, 'rb') as f: # Read as bytes
            return list(f.readlines()) # 返回字节行列表
    except FileNotFoundError:
        print(f"错误: M3U 文件未找到: {file_path}")
        sys.exit(1)
    except Exception as e:
        print(f"读取 M3U 文件时出错: {e}")
        sys.exit(1)

def check_url(url, timeout=10):
    """检查单个 URL 的可用性"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 M3U-Checker/1.0'
    }
    try:
        # 使用 HEAD 请求获取头部信息，更快
        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True, stream=False)
        status_code = response.status_code
        content_type_header = response.headers.get('Content-Type', '')
        content_type_cleaned = content_type_header.lower().split(';')[0].strip()

        if status_code == 200:
            if any(valid_ct == content_type_cleaned for valid_ct in VALID_CONTENT_TYPES):
                return True, f"有效 (状态: {status_code}, 类型: {content_type_header})"
            elif is_ignored_content_type(content_type_cleaned):
                 return False, f"无效 (状态: {status_code}, 类型: {content_type_header} - 通常不是流媒体)"
            else:
                # 对于未明确列出的类型，如果是 200 OK，可以先标记为可能有效，但需要注意
                print(f"警告: URL {url} 状态 200 OK，但 Content-Type '{content_type_header}' 未在已知有效列表中。")
                return True, f"可能有效 (状态: {status_code}, 类型: {content_type_header} - 未知)"

        elif 300 <= status_code < 400 : # 通常 allow_redirects=True 会处理，但有些情况可能仍返回3xx
            return False, f"无效 (重定向问题: {status_code}, 位置: {response.headers.get('Location', 'N/A')})"
        elif 400 <= status_code < 600:
            return False, f"无效 (HTTP 错误: {status_code})"
        else:
            return False, f"无效 (未知状态: {status_code}, 类型: {content_type_header})"

    except requests.exceptions.Timeout:
        return False, "无效 (连接超时)"
    except requests.exceptions.TooManyRedirects:
        return False, "无效 (重定向过多)"
    except requests.exceptions.ConnectionError as e:
        return False, f"无效 (连接错误: {str(e)})"
    except requests.exceptions.RequestException as e:
        return False, f"无效 (请求错误: {type(e).__name__} - {str(e)})"
    except Exception as e:
        return False, f"无效 (检查URL时发生未知错误: {str(e)})"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查 M3U 文件或 URL 中的链接可用性。")
    parser.add_argument("m3u_source", help="M3U 文件的 URL 或本地路径。")
    parser.add_argument("--timeout", type=int, default=10, help="每个流链接的请求超时时间 (秒)。")
    parser.add_argument("--download-timeout", type=int, default=30, help="下载M3U文件本身的超时时间 (秒) (如果源是URL)。")
    
    args = parser.parse_args()

    m3u_content_lines = [] # Store lines as bytes initially
    if args.m3u_source.startswith(('http://', 'https://')):
        m3u_content_lines = fetch_m3u_from_url(args.m3u_source, timeout=args.download_timeout)
    else:
        m3u_content_lines = read_m3u_from_file(args.m3u_source)

    if not m3u_content_lines:
        print("未能获取或读取 M3U 内容。")
        sys.exit(1)

    urls_to_check = parse_m3u_content(m3u_content_lines)
    
    if not urls_to_check:
        print("M3U 内容中没有找到可检查的 URL。")
        # 如果 M3U 本身获取成功但内容为空，则认为检查成功（没有无效链接）
        sys.exit(0)

    valid_links = 0
    invalid_links = 0
    checked_links_count = 0
    summary = []

    print(f"\n开始检查 {len(urls_to_check)} 个流链接...\n")

    for i, url in enumerate(urls_to_check):
        if not (url.startswith('http://') or url.startswith('https://') or url.startswith('rtmp://') or url.startswith('rtsp://')):
            # 你也可以将非 http/https 的链接标记为无效或特殊处理
            # rtmp/rtsp 通常无法用 requests.head() 检查，这里简单跳过
            # print(f"[{i+1}/{len(urls_to_check)}] 跳过非HTTP/HTTPS链接: {url}\n")
            # summary.append(f"[~] {url} - 非HTTP/HTTPS链接，跳过检查")
            continue # 跳过非 HTTP/HTTPS 链接

        checked_links_count += 1
        print(f"[{i+1}/{len(urls_to_check)}] (实际检查第 {checked_links_count} 个) 正在检查: {url}")
        is_valid, message = check_url(url, timeout=args.timeout)
        if is_valid:
            valid_links += 1
            print(f"  -> {message}\n")
            summary.append(f"[✓] {url} - {message}")
        else:
            invalid_links += 1
            print(f"  -> {message}\n")
            summary.append(f"[✗] {url} - {message}")

    print("\n--- 检查结果摘要 ---")
    for item in summary:
        print(item)
    
    print(f"\n总共解析链接数: {len(urls_to_check)}")
    print(f"实际检查HTTP/HTTPS链接数: {checked_links_count}")
    print(f"有效链接: {valid_links}")
    print(f"无效链接: {invalid_links}")

    if invalid_links > 0:
        print("\n检测到无效链接。")
        sys.exit(1)
    elif checked_links_count == 0 and len(urls_to_check) > 0:
        print("\n没有可检查的HTTP/HTTPS链接，但M3U中存在链接。")
        sys.exit(0) # 或者根据你的需求决定是否算失败
    else:
        print("\n所有可检查的链接均有效或可能有效。")
        sys.exit(0)
