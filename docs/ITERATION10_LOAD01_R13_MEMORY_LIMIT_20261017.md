# LOAD01 R13：启动通过后触发负载期内存保护

2026-10-17，bt_api_py 现有 dev。Root 执行时 HEAD b194a8a5d691f8298f49ac73a70ff2baf060895d；固定运行导出来自 f95d2056749e0aaaef69356db7d5344dc9c606b6 及六包 gitlink，期间只增加文档/另一测试及证据，不修改运行导出。交付父提交 890e9ee1dd9dbe748dddb24b7362912573369717，实际提交 SHA 见本文件 Git 历史。

用户授权 Root 代为释放内存。Root 只对核实身份的 ChatGPT/Codex/Chrome 进程执行一次工作集回收，没有关闭应用、标签或触碰 Qoder/retained session。内存回收仅改善当时可用物理内存，应用页面可重新载入。启动前三样本 12,171,644,928 / 12,050,862,080 / 11,924,418,560 B 均超过 10,603,200,512 B；runner 自身启动门记录 11,777,642,496 B。

Root 唯一实际 pilot：1 failed in 123.51s，exit 1；387 个外部来源/归档 pins 与七个 HEAD 前后相同。实际 runner 核验七档案、1209 Python成员；runtime manifest 为376项，二者不是同一个计数。首次外层 launcher 把1209误当376，停止于 pytest 之前；原脚本与有限诊断保留，仅修正该错误计数后启动了本次唯一 pilot。没有降低业务、时长、deadline、租约或资源保护线。

112 个子进程全部就绪，100策略、10账户、20行情主题；启动维护失败0，ready屏障可用内存4,770,299,904 B。进入 sustained-load 后，0/5/10/15.015/20.015秒采样的可用内存分别为4,759,273,472 / 3,905,921,024 / 2,922,057,728 / 2,009,198,592 / 1,888,423,936 B，最后两次低于2GiB，原保护触发 RuntimeError。112子进程RSS总和从8,915,431,424升至11,909,275,648 B；同期增加2,993,844,224 B。此相关性支持当前72MiB/子进程启动预测偏低，不能定位具体分配点，也不能把共享RSS总和当唯一物理内存。初始CPU采样的分母仅0.001秒，不能把23583.984375%投影视为实际机器负载。

没有完成120秒负载，更没有启动1800秒。normal_shutdown_complete=false，110个owner/strategy按测试失败清理路径被终止、exit=-15；market和monitor exit=0，但全部112个normal_exit投影为false。112句柄closed、cleanup.errors为空，不能据此声称优雅关闭。Root随后只读检查：记录中的112个PID及pytest PID均已不在进程表，采样时可用内存12,375,597,056 B；未操作任何其他进程。

stdout保留一条pytest配置warning，以及捕获的ZMQ Proactor事件循环RuntimeWarning；原始stderr文件为空，不能据此说运行期间无warning。按用户要求暂缓受阻重载，先查负载进程内存增长和按角色预测，不原样重跑、不降低2GiB保护。非均匀分布与P0实测性能阈值登记仍未完成。

[原始证据ZIP](evidence/iteration10-load01-pilot-r13-memory-20261017/root-evidence.zip)包含12件原运行输出及8件Root内存/启动/退出观察，均逐字节核对。证据等级为失败的本机合成pilot，不增加LOAD01正式PASS。DIM07由独立条款合并裁定另行通过，当前累计62/68。
