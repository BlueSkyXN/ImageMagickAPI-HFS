#!/usr/bin/env python3
"""
并发行为测试脚本
用于验证当前架构下的实际并发处理能力
"""

import asyncio
import aiohttp
import time
from datetime import datetime

async def send_request(session, request_id, url, file_path):
    """发送单个转换请求"""
    start_time = time.time()

    try:
        with open(file_path, 'rb') as f:
            data = aiohttp.FormData()
            data.add_field('file', f, filename='test.jpg')

            print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 请求{request_id}: 开始发送")

            async with session.post(url, data=data) as response:
                elapsed = time.time() - start_time
                status = response.status

                print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 请求{request_id}: "
                      f"完成 (状态: {status}, 耗时: {elapsed:.2f}s)")

                return {
                    'request_id': request_id,
                    'status': status,
                    'duration': elapsed,
                    'start': start_time
                }
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 请求{request_id}: "
              f"失败 ({str(e)}, 耗时: {elapsed:.2f}s)")
        return {
            'request_id': request_id,
            'status': 'error',
            'error': str(e),
            'duration': elapsed
        }

async def test_concurrent_requests(num_requests, url, file_path):
    """测试并发请求行为"""
    print(f"\n{'='*70}")
    print(f"测试场景: 同时发送 {num_requests} 个请求")
    print(f"目标URL: {url}")
    print(f"{'='*70}\n")

    async with aiohttp.ClientSession() as session:
        # 同时发送所有请求
        tasks = [
            send_request(session, i+1, url, file_path)
            for i in range(num_requests)
        ]

        test_start = time.time()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        test_duration = time.time() - test_start

        # 分析结果
        print(f"\n{'='*70}")
        print(f"测试结果分析")
        print(f"{'='*70}")
        print(f"总耗时: {test_duration:.2f}s")

        successful = [r for r in results if isinstance(r, dict) and r.get('status') == 200]
        print(f"成功: {len(successful)}/{num_requests}")

        if successful:
            durations = [r['duration'] for r in successful]
            print(f"平均响应时间: {sum(durations)/len(durations):.2f}s")
            print(f"最快响应: {min(durations):.2f}s")
            print(f"最慢响应: {max(durations):.2f}s")

            # 分析是否并行
            starts = [r['start'] for r in successful]
            time_spread = max(starts) - min(starts)

            print(f"\n并发模式分析:")
            print(f"  请求发送时间跨度: {time_spread:.2f}s")

            if time_spread < 1.0:
                print(f"  ✅ 请求几乎同时开始处理 → 并行模式")

                # 检查是否真正并行
                avg_duration = sum(durations) / len(durations)
                if test_duration < avg_duration * 1.5:
                    print(f"  ✅ 总时间({test_duration:.2f}s) ≈ 单个请求时间({avg_duration:.2f}s)")
                    print(f"     → 真正的并行处理")
                else:
                    print(f"  ⚠️  总时间({test_duration:.2f}s) > 单个请求时间({avg_duration:.2f}s)")
                    print(f"     → 并行但存在资源竞争")
            else:
                print(f"  ❌ 请求处理有明显延迟 → 存在排队效应")

async def main():
    """主测试函数"""
    # 配置
    BASE_URL = "http://localhost:8000"
    TEST_FILE = "test_image.jpg"  # 需要准备一个测试图片

    print("""
╔══════════════════════════════════════════════════════════════════╗
║         ImageMagick API 并发行为测试工具                          ║
╚══════════════════════════════════════════════════════════════════╝

测试说明:
1. 此脚本会发送多个并发请求到API
2. 记录每个请求的开始和结束时间
3. 分析是否为真正的并行处理

准备工作:
- 确保API服务运行在 http://localhost:8000
- 在当前目录放置测试图片 test_image.jpg
""")

    # 检查测试文件
    import os
    if not os.path.exists(TEST_FILE):
        print(f"❌ 测试文件不存在: {TEST_FILE}")
        print(f"请创建一个测试图片文件，或使用以下命令生成:")
        print(f"  convert -size 800x600 xc:blue {TEST_FILE}")
        return

    # 测试场景1: 3个并发请求（轻量级测试）
    await test_concurrent_requests(
        num_requests=3,
        url=f"{BASE_URL}/convert/jpeg/lossy/80",
        file_path=TEST_FILE
    )

    await asyncio.sleep(2)  # 等待服务器清理

    # 测试场景2: 10个并发请求（中等压力）
    await test_concurrent_requests(
        num_requests=10,
        url=f"{BASE_URL}/convert/webp/lossy/80",
        file_path=TEST_FILE
    )

    print(f"\n{'='*70}")
    print("结论:")
    print("  - 如果'总时间 ≈ 单个请求时间': 真正的并行处理")
    print("  - 如果'总时间 ≈ 单个请求时间 × 请求数': 串行排队处理")
    print("  - 如果'总时间'介于两者之间: 有限的并行（资源竞争）")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    asyncio.run(main())
