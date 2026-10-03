/**
 * Magick 图像转换器 - 前端交互逻辑
 * 功能：主题切换、文件上传、参数调整、表单提交
 */

(function() {
    'use strict';

    // ==========================================
    // 1. 主题切换功能
    // ==========================================
    const THEME_KEY = 'magick-theme-preference';
    const themeBtns = document.querySelectorAll('.theme-btn');
    const body = document.body;

    /**
     * 应用主题到页面
     * @param {string} theme - 主题名称 (apple|glass|minimal|tech)
     */
    function applyTheme(theme) {
        body.setAttribute('data-theme', theme);

        // 更新按钮激活状态
        themeBtns.forEach(btn => {
            if (btn.dataset.theme === theme) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });

        // 保存到 localStorage
        try {
            localStorage.setItem(THEME_KEY, theme);
        } catch (e) {
            console.warn('无法保存主题偏好:', e);
        }
    }

    /**
     * 加载用户保存的主题偏好
     */
    function loadThemePreference() {
        try {
            const savedTheme = localStorage.getItem(THEME_KEY);
            if (savedTheme && ['apple', 'glass', 'minimal', 'tech'].includes(savedTheme)) {
                return savedTheme;
            }
        } catch (e) {
            console.warn('无法读取主题偏好:', e);
        }
        return 'apple'; // 默认主题
    }

    // 初始化主题
    const initialTheme = loadThemePreference();
    applyTheme(initialTheme);

    // 绑定主题切换按钮事件
    themeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const theme = btn.dataset.theme;
            applyTheme(theme);
        });
    });

    // ==========================================
    // 2. 文件上传交互
    // ==========================================
    const fileInput = document.getElementById('fileInput');
    const selectedFile = document.getElementById('selectedFile');
    const fileInputWrapper = document.querySelector('.file-input-wrapper');

    /**
     * 显示已选择的文件名
     */
    fileInput.addEventListener('change', function() {
        if (this.files.length > 0) {
            const file = this.files[0];
            const fileName = file.name;
            const fileSize = (file.size / (1024 * 1024)).toFixed(2); // MB
            selectedFile.textContent = `✓ 已选择: ${fileName} (${fileSize} MB)`;
        } else {
            selectedFile.textContent = '';
        }
    });

    /**
     * 拖拽上传功能
     */
    fileInputWrapper.addEventListener('dragover', (e) => {
        e.preventDefault();
        fileInputWrapper.style.borderColor = 'var(--color-primary)';
        fileInputWrapper.style.background = 'var(--bg-input-hover)';
    });

    fileInputWrapper.addEventListener('dragleave', (e) => {
        e.preventDefault();
        fileInputWrapper.style.borderColor = 'var(--border-color)';
        fileInputWrapper.style.background = 'var(--bg-input)';
    });

    fileInputWrapper.addEventListener('drop', (e) => {
        e.preventDefault();
        fileInputWrapper.style.borderColor = 'var(--border-color)';
        fileInputWrapper.style.background = 'var(--bg-input)';

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            fileInput.files = files;
            // 触发 change 事件以显示文件名
            const event = new Event('change');
            fileInput.dispatchEvent(event);
        }
    });

    // ==========================================
    // 3. 质量参数滑块交互
    // ==========================================
    const slider = document.getElementById('setting');
    const sliderValue = document.getElementById('settingValue');
    const paramHint = document.getElementById('paramHint');
    const modeRadios = document.querySelectorAll('input[name="mode"]');

    /**
     * 更新参数提示文本
     */
    function updateHint() {
        const mode = document.querySelector('input[name="mode"]:checked').value;
        const value = parseInt(slider.value);
        sliderValue.textContent = value;

        if (mode === 'lossy') {
            // 有损模式：质量提示
            let quality = '中等';
            if (value >= 90) quality = '极高';
            else if (value >= 80) quality = '高';
            else if (value >= 60) quality = '中等';
            else if (value >= 40) quality = '中低';
            else quality = '低';

            paramHint.textContent = `质量: ${value} - ${quality}质量 (0=最低质量，100=最高质量)`;
        } else {
            // 无损模式：压缩速度提示
            let speed = '平衡';
            if (value <= 20) speed = '最慢/最佳压缩';
            else if (value <= 40) speed = '较慢/较好压缩';
            else if (value <= 60) speed = '平衡';
            else if (value <= 80) speed = '较快/较差压缩';
            else speed = '最快/最差压缩';

            paramHint.textContent = `压缩速度: ${value} - ${speed} (0=最慢/最佳，100=最快/最差)`;
        }
    }

    // 滑块移动时更新
    slider.addEventListener('input', updateHint);

    /**
     * 模式切换时自动调整质量值
     */
    modeRadios.forEach(radio => {
        radio.addEventListener('change', function() {
            const mode = document.querySelector('input[name="mode"]:checked').value;

            if (mode === 'lossless') {
                // 默认优先转换速度；像素无损性由转换模式决定。
                slider.value = 75;
            } else {
                // 有损模式：默认中等质量（80=高质量）
                slider.value = 80;
            }

            updateHint();
        });
    });

    // 初始化提示
    updateHint();

    // ==========================================
    // 4. 表单提交处理
    // ==========================================
    const form = document.getElementById('uploadForm');
    const submitBtn = form.querySelector('.submit-btn');
    const originalBtnText = submitBtn.textContent;

    form.addEventListener('submit', function(e) {
        // 显示加载状态
        submitBtn.textContent = '⏳ 转换中...';
        submitBtn.disabled = true;

        // 如果表单提交失败，需要恢复按钮状态
        // 这里使用 setTimeout 作为后备方案
        setTimeout(() => {
            if (submitBtn.disabled) {
                submitBtn.textContent = originalBtnText;
                submitBtn.disabled = false;
            }
        }, 330000); // 留出后端 300 秒处理预算及传输余量
    });

    // ==========================================
    // 5. 键盘快捷键
    // ==========================================
    document.addEventListener('keydown', function(e) {
        // Ctrl/Cmd + K: 聚焦文件输入
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            fileInput.click();
        }

        // Ctrl/Cmd + 1-4: 切换主题
        if ((e.ctrlKey || e.metaKey) && e.key >= '1' && e.key <= '4') {
            e.preventDefault();
            const themes = ['apple', 'glass', 'minimal', 'tech'];
            const themeIndex = parseInt(e.key) - 1;
            applyTheme(themes[themeIndex]);
        }
    });

    // ==========================================
    // 6. 工具函数 - 参数验证
    // ==========================================

    /**
     * 验证文件大小（客户端预检查）
     */
    fileInput.addEventListener('change', function() {
        if (this.files.length > 0) {
            const file = this.files[0];
            const maxSize = 200 * 1024 * 1024; // 200MB

            if (file.size > maxSize) {
                alert(`文件过大！最大支持 200MB，当前文件: ${(file.size / (1024 * 1024)).toFixed(2)} MB`);
                this.value = ''; // 清空选择
                selectedFile.textContent = '';
                return;
            }
        }
    });

    // ==========================================
    // 7. 初始化完成提示
    // ==========================================
    console.log('%c🧙‍♂️ Magick 图像转换器', 'font-size: 20px; font-weight: bold; color: #0071e3;');
    console.log('%c✨ 前端已加载完成', 'color: #30d158;');
    console.log('%c快捷键提示:', 'font-weight: bold;');
    console.log('  Ctrl/Cmd + K: 打开文件选择');
    console.log('  Ctrl/Cmd + 1-4: 切换主题 (1:Apple, 2:Glass, 3:Minimal, 4:Tech)');

})();
