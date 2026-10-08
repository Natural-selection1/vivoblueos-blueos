# Codex conversation

## User

话说为什么需要询问是否需要先支持哪种架构? loader与架构有关么?

## Assistant

**loader 的主体可以与架构无关，但它加载的 ELF、构造的启动环境，以及底层内存操作与架构有关。** 问“先支持哪个架构”，主要是为了确定第一轮联调和验收范围，不是要求分别写两套 loader。

按你的任务，可以分成三层：

| 层次           | 具体工作                                                             | 与架构的关系                          |
| -------------- | -------------------------------------------------------------------- | ------------------------------------- |
| 通用加载逻辑   | 读取文件、检查边界、遍历 `PT_LOAD`、拷贝、BSS 清零、解析 `PT_INTERP` | 基本可以共用                          |
| ELF 与启动 ABI | 校验 `e_machine`、位数、字节序、入口要求，构造栈和 auxv              | 根据目标架构与 ABI 配置               |
| 平台操作       | 建立用户映射、设置页权限、同步指令缓存、进入用户态                   | 由内存和架构模块实现，loader 调用接口 |

例如，同一个通用 loader 可以读取 AArch64 和 RISC-V ELF，但**当前运行在 AArch64 上的内核，必须拒绝把 RISC-V 指令当成本机程序执行**。因此，解析逻辑可以通用，接受什么 ELF 却需要目标平台提供规则。

用户栈也是类似情况：`argc → argv → envp → auxv` 的总体结构可以共用，但写入的整数和指针宽度、字节序、最终 SP 对齐，以及部分辅助信息，要符合目标 ABI。这里还要区分：**有些差异来自 CPU 架构，有些来自操作系统和运行时约定**，并非换架构就一定要换整套栈布局。

最底层的差异更明显：

- loader 提出“这段用户内存应当可读、可执行”，内存模块将它转换成对应架构的页表属性。
- loader 写入了指令，平台模块保证 CPU 后续能正确取到这些指令。
- loader 返回用户入口和 SP，process/arch 模块准备寄存器并进入用户态。

**这些平台操作需要完成，但不意味着都由你实现。** 按目前分工，你更应该明确调用接口和返回信息。

另外，原来 loader 中的 AArch64、RISC-V **重定位算法确实与架构有关，但按《loader流程说明》，这部分属于用户态 `ld.so`，不应成为你当前内核 loader 的实现负担。**

所以，“先 A64 还是 RV”真正影响的是：

1. 首个测试 ELF 用什么工具链生成、遵循什么启动 ABI。
2. 哪个平台的用户地址空间和进入用户态路径先能对接。
3. 第一个端到端测试在哪个平台通过。

它**不阻塞你现在编写通用加载逻辑、解释器处理和参数化的用户栈构造器**。我之前把它列为需要确认的事项，是为了确定联调顺序；如果说成“必须先确定架构才能开始 loader”，那就过重了。

比较合适的做法是：**公共逻辑按可支持 A64/RV 的方式组织，第一轮选择配套能力最先就绪的平台打通，再验证另一个平台。**

## User

关于"- loader 提出“这段用户内存应当可读、可执行”，内存模块将它转换成对应架构的页表属性。"
这部分, 是我实现loader的过程中需要考虑的事情么?

## Assistant

**需要考虑，但你负责的是“正确表达权限需求”，页表怎么实现通常由内存模块负责。** 而且《loader流程说明》第 4 项明确要求 loader 设置段权限，所以这属于你的职责边界。

具体来说，你需要做三件事：

1. **从 ELF 中读取权限。** 每个 `PT_LOAD` 的 `p_flags` 包含 `PF_R`、`PF_W`、`PF_X`。loader 据此知道哪些段允许读、写、执行，不能把所有段都映射成 RWX。

2. **通过内存接口落实权限。** 接口形式与许昌协商，例如下面只是示意：

   ```rust
   address_space.protect(user_range, permissions)?;
   ```

   你传入用户地址范围和通用权限；内存模块负责转换成 A64/RV 页表属性、维护相关硬件状态。**不需要你在 loader 里直接操作页表位。**

3. **处理权限设置的时机和错误。** loader 必须能先完成段内容写入，再保证进入用户态前权限正确。如果设置失败，加载不能报告成功。

这里有两个需要提前对接的细节：

- **权限按页生效，ELF 段不一定按页边界结束。** 两个段涉及同一页时，不能分别设置后互相覆盖，也不能随意合并出 RWX。需要约定由谁生成页级权限方案，以及无法满足时是否拒绝加载。
- **内核能写入，不等于用户态需要写权限。** 代码段可以通过内核映射填充，最终让用户只能读和执行。具体写入方式由内存接口提供，不必为了拷贝代码而开放用户写权限。

另外，动态程序的 **RELRO 收紧权限应在用户态重定位完成后处理**，不能直接照搬施涵格原来“内核重定位后立即 seal”的时机。

因此，你现在就可以实现 **ELF 权限解析、权限请求以及失败处理**，用模拟后端检查请求是否正确。真正修改页表，需要与许昌的接口对接；当前 `FlatImageMemory` 的 `LogicalOnly` 只能用于这类逻辑验证，不能作为用户进程权限已经落实的依据。

## Activity

    Worked for 19s • 16:25

## User

讲讲现在施涵格添加的这些类型都是什么东西? 它所写的整个链路是如何工作的?

## Assistant

我会沿着“打开 ELF → 加载和链接 → 创建 application 线程 → 退出回收”的路径，把主要类型按职责串起来，重点解释它们为什么存在、由谁持有，以及阶段之间传递什么。

## Assistant

施涵格这版代码可以分成三块：**单个 ELF 的加载流水线、多 ELF 的动态链接器，以及管理应用启动与退出的 application 层。**

类型很多，主要是因为他把“加载到哪一步了”“谁负责这块内存”“谁提供平台能力”分别编码进了 Rust 类型。理解这些区别之后，就不用逐个死记结构体。

先看整体关系：

```text
ApplicationService                         应用启动入口
    │
    ├─ ApplicationManager                  分配应用记录、管理状态
    ├─ NamespaceLoadPlanner                查找主程序及共享库依赖
    │
    └─ ApplicationLoader                   组装加载器与内核接口
           │
           └─ DynamicLinker                多 ELF 加载、符号解析、重定位
                  │
                  ├─ 单 ELF 加载阶段        校验、规划、分配、拷贝、清零
                  ├─ FlatImageMemory       提供内核堆内存
                  ├─ ArchitectureCodeCache 缓存同步
                  └─ KernelLinkPublisher   转交已加载资源的所有权
           │
           ▼
       LinkProduct                         入口、生命周期计划、资源凭据
           │
           ▼
ApplicationStartStorage + ThreadGroup
           │
           ▼
创建线程 → _start → librs 初始化 → main
           │
           ▼
退出协调 → ApplicationReaper 回收
```

**这里讲的是他现在的实现。整个动态链接过程仍在内核中，最后运行的是共享地址空间中的 application 线程。**

---

先认识最基础的类型：它们用来描述“输入是什么、地址是什么意思”。

| 类型            | 可以理解成什么                                                 |
| --------------- | -------------------------------------------------------------- |
| `ElfReader`     | “从 ELF 文件的指定偏移读取字节”的接口                          |
| `VfsElfReader`  | 使用内核 VFS 实现上述读取接口                                  |
| `LoadProfile`   | 本次允许加载的目标格式：架构、位数、字节序、ELF 类型、入口要求 |
| `LoadLimits`    | 一个 ELF 可以消耗多少资源，例如头表、段、元数据数量限制        |
| `LoadRequest`   | `LoadProfile + LoadLimits`，组成一次加载请求                   |
| `LoadPolicy`    | 当前加载路径允许哪些 ELF 特性，例如解释器、依赖、重定位形式    |
| `SessionLimits` | 多 ELF 一起链接时的总资源限制                                  |

`LoadProfile` 和 ELF 头的区别很重要：

- ELF 头说：“我是 AArch64 ELF64。”
- `LoadProfile` 说：“本次运行环境只接受 AArch64 ELF64。”

加载器负责核对两者，不能因为文件声称自己是什么，就自动接受什么。相关定义在 [identity.rs](/home/hegui/vivoblueos/blueos/kernel/loader/src/identity.rs:227)。

地址也被拆成了不同类型：

| 类型               | 含义                                 |
| ------------------ | ------------------------------------ |
| `TargetAddress`    | ELF 所描述或装载后使用的目标地址数值 |
| `TargetRange`      | 目标地址范围                         |
| `FileRange`        | 文件中的偏移与长度                   |
| `AllocationOffset` | 相对于某次内存分配起点的偏移         |

例如，同一段内容可以同时有：

```text
文件位置：       offset = 0x1000
ELF 中的位置：  p_vaddr = 0x2000
装载后的地址：  load_bias + 0x2000
分配区内偏移：  装载后的地址 - allocation.base
```

这些类型用来减少地址混淆。不过，`TargetAddress` **不是可以直接解引用的指针**，也没有单独区分“ELF 原始地址”和“装载后地址”；这部分仍需要看字段语义。

---

**第二组类型负责提供能力，也就是 loader 与外部环境之间的接口。**

| 接口                    | loader 向它提出什么要求                  | 当前 application 使用的实现 |
| ----------------------- | ---------------------------------------- | --------------------------- |
| `ImageMemory`           | 分配、读写、清零、失败撤销、最终释放     | `FlatImageMemory`           |
| `ImageProtectionMemory` | 查询权限能力、检查并应用权限             | 仍由 `FlatImageMemory` 实现 |
| `CodeCache`             | 让写入后的指令满足执行所需的缓存同步要求 | `ArchitectureCodeCache`     |
| `ArchRelocator`         | 按具体架构规则解释和执行重定位           | A64、ARM、RV 对应实现       |
| `ArtifactResolver`      | 根据共享库依赖，找到对应文件或已加载映像 | `NamespaceArtifactResolver` |
| `LinkPublisher`         | 接收整次链接完成后的资源所有权           | `KernelLinkPublisher`       |

这里的设计价值是：**loader 不必知道 VFS 怎样打开文件、内存来自哪种分配器，也不必把所有架构的重定位规则混在一起。**

例如：

```rust
MappedImage<R, M>
```

其中：

- `R` 是某种 `ElfReader` 实现；
- `M` 是某种 `ImageMemory` 实现。

不是一个 `R` 就代表一个运行线程，而是“这个映像用什么东西读文件、用什么东西访问内存”。

当前 `FlatImageMemory` 用内核堆保存映像，权限结果是 `LogicalOnly`。所以接口已经留好了扩展位置，但**当前后端没有因此变成用户地址空间实现**。

---

**第三组是最容易让人困惑的：同一个 ELF 为什么有这么多 `…Image` 类型？**

因为它使用了“类型状态”设计：**不同类型表示已经完成不同阶段，只有完成上一阶段，才能调用下一阶段的方法。**

单映像入口 [prepare_image](/home/hegui/vivoblueos/blueos/kernel/loader/src/lib.rs:147) 的核心调用是：

```rust
ImageLoader::new(reader, request)
    .admit()?
    .inspect()?
    .plan()?
    .allocate(memory)?
    .map()?
    .decode()?
    .relocation(relocator)?
    .cache(cache)?
    .seal()?
```

对应关系如下：

| 当前类型         | 表示什么已经完成             | 下一步                                    |
| ---------------- | ---------------------------- | ----------------------------------------- |
| `ImageLoader`    | 持有 reader 和加载请求       | `admit`：读取、校验 ELF 头                |
| `AdmittedImage`  | ELF 头通过准入检查           | `inspect`：检查 Program Header 和相关特性 |
| `InspectedImage` | 已收集并检查段等信息         | `plan`：检查布局、入口，计算装载范围      |
| `PlannedImage`   | 已确定内存需求和装载约束     | `allocate`：向后端申请存储                |
| `AllocatedImage` | 已取得内存并计算 load bias   | `map`：拷贝段内容、清零 BSS               |
| `MappedImage`    | 映像字节已就位               | `decode`：解析动态链接元数据              |
| `DecodedImage`   | 已整理出重定位等信息         | `relocation`：执行单映像支持的重定位      |
| `RelocatedImage` | 重定位完成                   | `cache`：同步指令缓存                     |
| `CachedImage`    | 缓存同步完成                 | `seal`：检查并应用权限                    |
| `SealedImage`    | 这条流水线要求的准备工作完成 | 转为对外的准备结果                        |

**这些并不是同时保留十份 ELF。** 方法通常消耗旧对象的 `self`，把 reader、元数据、内存事务等移动到新对象。

比如：

```text
AllocatedImage --map(self)--> MappedImage
```

完成转换后，旧的 `AllocatedImage` 已被消耗，调用者不能再拿它重复执行 `map`。

这既表达顺序，也降低重复重定位、未完成加载就发布等错误出现的机会。

还要注意：这里的 `map` 主要表示“把 ELF 段内容放到分配好的存储中”，**不等于它一定建立了 MMU 页表映射**。实际效果取决于内存后端。

---

**第四组类型处理内存所有权：出了错谁回收，成功后交给谁？**

这是这版代码很大一部分复杂度的来源。[memory.rs](/home/hegui/vivoblueos/blueos/kernel/loader/src/memory.rs:88) 中最重要的是：

| 类型                    | 职责                                               |
| ----------------------- | -------------------------------------------------- |
| `AllocationRequest`     | 申请要求：位置、大小、对齐                         |
| `Placement`             | 可以任选地址，还是必须使用固定地址                 |
| `ImageAllocation`       | 分配结果的描述：编号、基址、长度、对齐、所有权类别 |
| `AllocationLease`       | 对这次分配执行提交或撤销的唯一凭据                 |
| `ImageLoadTransaction`  | 加载期间持有凭据，失败时调用后端撤销               |
| `MutationProgress`      | 记录是否仅预留、已写入字节、已修改权限             |
| `AllocationRollbackLog` | 多映像场景中，记录需要一起回滚的分配               |

最关键的区别是：

> `ImageAllocation` 描述“哪块内存”；`AllocationLease` 表示“谁有权处置这块内存”。

`ImageAllocation` 可以复制，因为多个操作都需要描述同一块内存；`AllocationLease` 不能随便复制，否则两份凭据可能造成重复释放。

单映像资源大致这样流动：

```text
后端分配
    ↓
AllocationLease
    ↓
ImageLoadTransaction 持有
    ├─ 中途出错或放弃 → Drop 调用 abort_image
    └─ 加载成功 → PreparedImage
                     ↓ prepare_commit()
                ReadyImageCommit
                     ↓ commit()
                后端接管资源
```

`PreparedImage` 表示“已加载好，但尚未完成资源交接”。`ReadyImageCommit` 表示“交接前可能失败的准备工作也已完成”。

为什么还要分成两步提交？因为他希望：

- 可能失败的检查、分配在 `prepare_commit` 阶段完成；
- 真正 `commit` 时只移动已经准备好的资源，避免交接一半后失败。

这里的 commit **不是开始执行程序**，只是完成所有权交接。另外，自动回滚由事务或会话守卫实现，不是裸 `AllocationLease` 自己就会释放内存。

---

你前面问的权限问题，在代码中又被分成了几个类型：

| 类型                                        | 作用                               |
| ------------------------------------------- | ---------------------------------- |
| `MemoryPermissions`                         | 通用的读、写、执行等权限           |
| `ProtectionCapabilities`                    | 后端支持的保护粒度、范围数量等限制 |
| `SealRange` / `SealPlan`                    | 计划给哪些地址范围设置什么权限     |
| `PreparedProtectionPlan`                    | 根据后端能力检查、整理后的权限方案 |
| `ProtectionBatch`                           | 提交给后端的一批权限操作           |
| `ProtectionRecord` / `AppliedProtectionSet` | 记录请求范围、实际范围及实施结果   |
| `ProtectionLevel`                           | 区分硬件落实和仅逻辑记录           |

它的关系是：

```text
ELF 段权限、RELRO 等信息
    ↓
生成 SealPlan
    ↓
结合后端保护粒度等能力检查
    ↓
生成 PreparedProtectionPlan
    ↓
调用后端应用权限
    ↓
记录 AppliedProtectionSet
```

所以，`seal` 并不是“给 ELF 加密”或者“关闭文件”，而是**在当前流水线中完成执行前的权限处理，并记录结果**。

当前实现还在这里处理重定位后的 RELRO 收紧。这也是以后拆出“内核只加载、用户态重定位”路径时需要调整的地方。相关类型集中在 [seal.rs](/home/hegui/vivoblueos/blueos/kernel/loader/src/image/seal.rs:31)。

---

**第五组类型把多个 ELF 组织成一次动态链接。**

假设主程序需要 `libc.so.1`，后者还可能需要其他共享库，仅把主程序加载好是不够的。还需要知道依赖关系，以及一个未定义符号应当从哪个库找到。

这部分主要类型是：

| 类型                                   | 含义                                               |
| -------------------------------------- | -------------------------------------------------- |
| `ResolvedArtifact`                     | 已找到的 ELF 输入，包括身份、角色、reader 等       |
| `ArtifactIdentity`                     | 文件在依赖图中的身份，用于识别和去重               |
| `ArtifactRole`                         | 主程序还是共享库                                   |
| `DependencyName` / `DependencyRequest` | 需要哪个库，以及谁提出这个依赖                     |
| `ImageId`                              | 链接过程中给映像分配的编号                         |
| `DependencyGraph`                      | 哪个 ELF 依赖哪个 ELF                              |
| `SymbolTable`                          | 某个 ELF 的符号信息                                |
| `ScopeSet`                             | 查找符号时，各映像应按什么范围和顺序搜索           |
| `RelocationBinding`                    | 记录某次符号重定位最终选中了哪个提供者             |
| `InitPlan` / `FiniPlan`                | 初始化、析构函数的执行计划                         |
| `LinkProduct`                          | 链接完成后的入口、映像信息、生命周期计划、资源凭据 |

`DynamicLinker` 是组织这项工作的入口，`LinkSession` 是一次正在进行的链接。

[会话定义](/home/hegui/vivoblueos/blueos/kernel/loader/src/dynamic_linker/session.rs:275) 中：

```rust
LinkSession<'a, M, S, A>
```

可以读成：

- `'a`：这次会话借用内存后端的生命周期；
- `M`：内存后端；
- `S`：目前处于哪个链接阶段；
- `A`：架构重定位实现。

它同样使用阶段类型：

```text
DynamicLinker.begin(...)
    ↓
BuildingSession
    │ close_dependencies：找到并加载完整依赖
    │ freeze_scopes：确定符号查找范围
    ↓
ScopedSession
    │ relocate：跨映像解析符号并重定位
    ↓
RelocatedSession
    │ seal：缓存同步、权限处理
    ↓
SealedSession
    │ publish：转交资源所有权
    ↓
LinkProduct<Receipt>
```

**这里有两条不同的入口，不能混为一谈：**

- `prepare_image`：完成一个映像的单映像流水线。
- `DynamicLinker`：复用前面的加载阶段，先得到多个映像的动态元数据，再统一完成跨映像链接。

多映像路径不会简单地对每个库调用完整 `prepare_image`，因为某个映像的重定位可能要等待其他库的符号就绪。

会话中途失败时，回滚守卫会撤销已经接管的分配；成功发布时，这些资源统一转交给 publisher。

---

**第六组是 application 层，它负责把“链接结果”变成“一个活着的应用”。**

| 类型                      | 职责                                                  |
| ------------------------- | ----------------------------------------------------- |
| `ApplicationService`      | 启动、退出等操作的总入口                              |
| `ApplicationManager`      | 管理应用记录和 `Loading/Running/Stopping` 等状态      |
| `ApplicationHandle`       | 槽位编号加代数，防止旧句柄误指向后来创建的应用        |
| `ApplicationNamespace`    | 固定本次启动的路径、依赖查找环境等；不是 MMU 地址空间 |
| `NamespaceLoadPlanner`    | 预先扫描完整依赖图，生成 `NamespaceLoadPlan`          |
| `ApplicationLoader`       | 把动态链接器、VFS、内存、系统库注册表等组装起来       |
| `ThreadGroup`             | 持有应用线程成员及应用资源                            |
| `ApplicationStartStorage` | 持有启动参数、auxv、初始化/析构计划的实际存储         |
| `ApplicationReaper`       | 等应用满足回收条件后释放资源                          |

有两个名字特别容易误解。

**`ThreadGroup` 不是已经实现好的进程。** 它把多个线程及相关资源归在一起，具有一些进程管理需要的生命周期机制，但当前没有因此获得独立用户地址空间。

**`ApplicationStartStorage` 不是用户栈。** 它在内核堆中保存参数字符串、指针数组等，并提供 `BlueOsApplicationStartInfo` 指针给启动代码。

另外，application 中也有一个 `PlannedImage`，它表示“依赖预扫描中发现的某个文件”；与 loader 内部表示“单 ELF 内存布局已规划完成”的同名类型不是一回事。

---

用启动一次 hello 的例子，可以把完整链路串起来。

1. **`ApplicationService::spawn` 接收路径、argv、envp。**
   创建 namespace，由 manager 分配应用记录与 `ThreadGroup`。加载工作在一个单独的内核工作线程中进行。

2. **`NamespaceLoadPlanner` 预扫描依赖。**
   读取主程序及依赖的 `DT_NEEDED`，生成完整计划。先扫描还有一个实际目的：提前知道本次要用哪些共享系统库，以便批量协调加载权限，避免并发启动时相互等待。

3. **`ApplicationLoader` 驱动 `DynamicLinker`。**
   通过 VFS 找到文件，在 `FlatImageMemory` 中加载各映像，完成符号解析、重定位、缓存同步和权限处理。

4. **`KernelLinkPublisher` 接收加载资源。**
   返回的 `LinkProduct<KernelLinkReceipt>` 同时包含执行信息与资源凭据。这里的 `Receipt` 不是单纯的成功标志，而是后续持有、回收映像的依据。

5. **构造启动信息，创建应用主线程。**
   `ApplicationStartStorage` 根据参数和链接结果准备启动数据；资源装入 `ThreadGroup`。随后创建线程，将入口设置成 ELF 入口，将启动信息指针作为参数传入，加入调度队列。实际代码在 [service.rs](/home/hegui/vivoblueos/blueos/kernel/kernel/src/application/service.rs:311)。

6. **`_start` 把控制权交给 librs 启动函数。**
   [\_\_librs_start_main](/home/hegui/vivoblueos/blueos/librs/src/lib.rs:115) 校验启动信息，初始化运行时，执行 init plan，报告 `ApplicationInitComplete`，再调用 `main(argc, argv, envp)`。因此，内核链接器负责生成初始化计划，实际函数由应用启动代码调用。

7. **`main` 返回后协调退出。**
   禁止新增线程并等待其他成员，执行 atexit、fini 和相关清理，最后结束主线程。reaper 在满足条件后回收应用资源，避免线程还在执行时就释放代码。

系统库还有一套额外机制：`SystemDsoRegistry` 记录共享库的加载、初始化和引用状态。`LoadPermit` 等类型协调谁负责首次加载；`SystemDsoLease` 表示某个应用持有对系统库的引用；`PublishedImageDescriptor` 描述已经加载的映像。后续应用可以复用就绪的库，而不用重新加载和重定位。这也是当前共享地址空间模型带来的复杂度。

---

对你接下来实现符合最高设计的 loader，**最值得先读透的是：`LoadRequest`、地址类型、`ImageMemory`、单映像前半段流水线，以及事务回滚机制。**

建议阅读顺序是：

1. [lib.rs 的加载入口](/home/hegui/vivoblueos/blueos/kernel/loader/src/lib.rs:147)：先看流程。
2. [identity.rs](/home/hegui/vivoblueos/blueos/kernel/loader/src/identity.rs:227) 和 [memory.rs](/home/hegui/vivoblueos/blueos/kernel/loader/src/memory.rs:237)：看输入约束与平台接口。
3. `image/admit.rs → inspect.rs → plan.rs → allocate.rs`：看 ELF 如何变成内存中的段。
4. [service.rs](/home/hegui/vivoblueos/blueos/kernel/kernel/src/application/service.rs:311)：看结果如何交给应用管理。
5. 最后再深入 `dynamic_linker` 和系统库注册表：理解现有行为及职责边界。

你需要保留和借鉴的是加载校验、内存抽象、资源交接这些基础；内核中的符号解析与重定位、共享 DSO 管理和自定义启动协议，则需要按《loader流程说明》重新安排职责。

## User

你觉得如果把redox的clone到本地, 它的相关代码会具有高参考价值么? 请你调查一下redox这一块大概是怎么实现的?

## Assistant

**有较高参考价值，尤其值得看 `relibc`；但 Redox 的职责划分与你们不同，适合参考具体机制，不适合直接照搬整体架构。**

我检查了官方 GitHub 镜像的源码快照：`relibc@650cbecb`、`kernel@b68957d4`。最重要的发现是：**Redox 普通程序的 ELF 加载也在用户态完成，而你们要求这部分放在内核。** Redox 的 `execve` 实现明确说明了这一点，并将核心加载工作交给 `redox_rt::proc::fexec_impl`。[exec.rs](https://github.com/redox-os/relibc/blob/650cbecb06e6123aec83296e41e5f97b93906fdf/src/platform/redox/exec.rs#L101)

它与你们的分工对比如下：

| 工作                                 | 你们的最高设计 | 当前 Redox                    |
| ------------------------------------ | -------------- | ----------------------------- |
| 解析主 ELF、加载段                   | 内核 loader    | 用户态 `redox-rt`             |
| 加载 `PT_INTERP` 指定的解释器        | 内核 loader    | 用户态 `redox-rt` 配合 relibc |
| 构造初始栈                           | 内核 loader    | 用户态 `redox-rt`             |
| 建立映射、切换地址空间、设置线程入口 | 内核           | 内核                          |
| 依赖加载、符号解析、重定位           | 用户态 `ld.so` | 用户态 `ld.so`                |

因此，你要找的许多“loader 实现参考”，**实际在 `relibc/redox-rt/src/proc.rs`，不在 Redox 内核里。**

它的普通程序执行链路大致是：

```text
当前用户程序调用 execve
    ↓
relibc：打开可执行文件、整理参数
    ↓
redox-rt：创建新的地址空间
    ↓
加载主 ELF
    ├─ 无 PT_INTERP：继续准备启动环境
    └─ 有 PT_INTERP：保留主程序信息，加载解释器
    ↓
构造新程序的 argc / argv / envp / auxv
    ↓
向内核提交：新地址空间、入口 PC、用户 SP
    ↓
内核安装新地址空间和寄存器状态
    ↓
无解释器：进入主程序
有解释器：进入 ld.so，自举、加载依赖、重定位后进入主程序
```

**其中最值得你参考的，是下面四个实现细节。**

1. **目标地址空间中的地址，与加载器用于写入的地址分开处理。**

   `fexec_impl` 为目标程序建立映射，再通过 `MmapGuard::map_mut_anywhere`，把目标地址空间中的相关内存临时映射到当前加载器可写的位置，然后读取文件内容填进去。

   这正好对应你之前讨论的问题：目标代码段可以有执行权限，加载器通过另一条可写映射填充内容。以后在 BlueOS 中，这个“临时可写位置”可能是内核映射。

   Redox 的具体 FD 接口不必照搬，**区分目标地址与写入地址的思路很值得借鉴**。[段加载与临时映射](https://github.com/redox-os/relibc/blob/650cbecb06e6123aec83296e41e5f97b93906fdf/redox-rt/src/proc.rs#L206)

2. **解释器加载时，显式保留主程序信息。**

   遇到 `PT_INTERP` 后，它返回 `FexecResult::Interp`，并通过 `InterpOverride` 保存主程序入口、Program Header 信息以及已创建的地址空间句柄。上层打开解释器，再使用这些信息继续装载。

   所以，加载解释器不会把主程序的信息覆盖掉。构造 auxv 时，`AT_ENTRY` 仍取主程序入口，`AT_BASE` 取解释器基址。

   对你来说，可参考的是“主程序装载结果”和“解释器装载结果”分别保存，而不是一定要采用它的递归调用结构。[InterpOverride 与解释器交接](https://github.com/redox-os/relibc/blob/650cbecb06e6123aec83296e41e5f97b93906fdf/redox-rt/src/proc.rs#L260)

3. **加载完成与真正切换执行，存在明确的交接点。**

   用户态加载器将新地址空间、PC、SP 提交给内核。在这版实现中，关闭对应控制句柄会触发切换；内核修改线程保存的指令指针、栈指针和地址空间。

   这个具体控制协议是 Redox 特有的，但它验证了你们可以采用的接口边界：

   ```text
   loader 输出：准备好的地址空间内容 + initial_pc + initial_sp
   process 接管：安装地址空间与寄存器，开始执行
   ```

   你不需要把调度器和进入用户态的代码塞进 loader。[内核地址空间交接代码](https://github.com/redox-os/kernel/blob/b68957d4a3e1590c4f5b8d5608d94b3e45bd3b45/src/scheme/proc.rs#L438)

4. **`ld.so` 必须先让自己能够运行，再链接其他程序。**

   Redox 的 `relibc_ld_so_start` 从初始栈读取 auxv，确定自身基址，先做自重定位，再进入第二阶段，初始化运行环境并创建 `Linker`。源码特别强调：自重定位之前，不能随意引用尚未解析的外部符号。[动态链接器自举](https://github.com/redox-os/relibc/blob/650cbecb06e6123aec83296e41e5f97b93906fdf/src/ld_so/start.rs#L162)

   后续 `Linker` 加载依赖，调用各 DSO 的重定位逻辑，并安排初始化。解释器入口汇编最后恢复初始栈，跳向返回的程序入口。[Linker](https://github.com/redox-os/relibc/blob/650cbecb06e6123aec83296e41e5f97b93906fdf/src/ld_so/linker.rs#L574)、[解释器入口汇编](https://github.com/redox-os/relibc/blob/650cbecb06e6123aec83296e41e5f97b93906fdf/ld_so/src/lib.rs#L6)

   这也说明：**施涵格的 `DynamicLinker` 算法即使能够迁到用户态，也还缺少“作为解释器开始执行”的自举和平台适配工作。**

有一个与你“第一个进程”特别相关的区别，也要单独指出：**Redox 的第一个用户态 bootstrap，并不走上述完整的普通 ELF 加载流程。**

我查到的内核 `usermode_bootstrap` 使用专门准备的 bootstrap 映像：映射并复制整块内容，从固定位置读取入口，设置寄存器；源码还明确说明它从没有栈的最小环境开始。这是一条特殊启动协议，不能作为你们 `PT_LOAD + 用户栈 + PT_INTERP` 设计的直接模板。[usermode_bootstrap](https://github.com/redox-os/kernel/blob/b68957d4a3e1590c4f5b8d5608d94b3e45bd3b45/src/syscall/process.rs#L109)

**如果要 clone，建议优先下载这两个仓库：**

| 仓库 / 文件                                     | 对你的参考价值                                           |
| ----------------------------------------------- | -------------------------------------------------------- |
| `relibc/redox-rt/src/proc.rs`                   | **最高**：段加载、地址空间写入、解释器交接、初始栈       |
| `relibc/src/platform/redox/exec.rs`             | 看上层如何打开文件并继续解释器加载                       |
| `relibc/src/crt0/src/lib.rs`                    | 看程序 `_start` 如何把初始 SP 交给运行时；有 A64/RV 分支 |
| `relibc/ld_so/src/lib.rs`、`src/ld_so/start.rs` | 理解解释器对 loader 提供的启动环境有什么要求             |
| `kernel/src/scheme/proc.rs`                     | 看加载结果与内核地址空间、线程状态的交接                 |
| `relibc/src/ld_so/linker.rs`、`dso.rs`          | 以后处理用户态动态链接器时深入阅读                       |

直接 clone [relibc](https://github.com/redox-os/relibc) 和 [kernel](https://github.com/redox-os/kernel) 就能覆盖这些内容。`redox-os/redox` 本身主要是整个系统的构建仓库，当前阅读目标不需要先把完整系统和所有依赖拉下来。[Redox 构建仓库说明](https://github.com/redox-os/redox)

最后，**它适合当参考实现，但不能替代你自己的校验和测试。** 例如，所查 `fexec_impl` 明确注明尚未处理两个段共享同一页的情况；该函数还有若干 `expect` 和 TODO。你可以学习它如何组织操作，但不要因此削弱施涵格现有的边界检查和失败回滚。[共享页限制](https://github.com/redox-os/relibc/blob/650cbecb06e6123aec83296e41e5f97b93906fdf/redox-rt/src/proc.rs#L213)

我的建议是：**值得 clone，先集中读 `redox-rt/src/proc.rs`，把其中“映像装载、解释器信息、栈构造、执行交接”四部分，与施涵格的现有类型逐项对照。** 这比从 Redox 内核启动代码一路往下读，更直接服务于你现在的任务。
