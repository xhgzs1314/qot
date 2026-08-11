const uploadArea = document.getElementById('upload-area');
const fileInput = document.getElementById('file-input');
const fileInfo = document.getElementById('file-info');
const resultArea = document.getElementById('result-area');
const resultBody = document.getElementById('result-body');
const errorTip = document.getElementById('error-tip');
const exportBtn = document.getElementById('export-btn');
const exportBtn2 = document.getElementById('export-btn2');
const typeMap = {
    'int': 'int',
    'float': 'float',
    'double': 'double',
    'char': 'char',
    'void': 'void',
    'str': 'str',
    'long': 'long',
    'bool': 'bool',
    'size_t': 'size_t',
    'byte': 'byte',
    'wca': 'wca',
    'int*': 'int',
    'float*': 'float',
    'double*': 'double',
    'char*': 'str',
    'void*': 'void',
    'LPCWSTR': 'wca',
    'LPWSTR': 'wca',
    'const char*': 'str',
    'const wchar_t*': 'wca'
};



uploadArea.addEventListener('click', () => {
    fileInput.click();
});


uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('active');
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('active');
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('active');
    if (e.dataTransfer.files.length > 0) {
        fileInput.files = e.dataTransfer.files;
        handleFile(fileInput.files[0]);
    }
});


fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
        handleFile(fileInput.files[0]);
    }
});


function handleFile(file) {

    errorTip.textContent = '';
    resultArea.style.display = 'none';


    const allowedTypes = ['.h', '.hpp', '.cpp', '.c'];
    const fileExt = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowedTypes.includes(fileExt)) {
        errorTip.textContent = `不支持的文件类型：${fileExt}，请上传.h/.hpp/.cpp/.c文件`;
        return;
    }


    fileInfo.textContent = `已选择：${file.name} (${formatFileSize(file.size)})`;


    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const content = e.target.result;
            const exportFunctions = parseExportFunctions(content);
            displayResults(exportFunctions);
        } catch (err) {
            errorTip.textContent = `解析失败：${err.message}`;
        }
    };
    reader.onerror = () => {
        errorTip.textContent = '文件读取失败，请重试';
    };
    reader.readAsText(file);
}

function parseExportFunctions(content) {
    const functions = [];


    const exportMacroRegex = /#define\s+(\w+)\s+__declspec\s*\(\s*dllexport\s*\)/g;
    const exportMacros = new Set();
    let macroMatch;

    while ((macroMatch = exportMacroRegex.exec(content)) !== null) {
        exportMacros.add(macroMatch[1]);
    }

    exportMacros.add('__declspec(dllexport)');


    const macroList = Array.from(exportMacros).map(macro =>

        macro.replace(/[()]/g, '\\$&')
    ).join('|');


    const functionRegex = new RegExp(
        `(${macroList})\\s+([\\w\\s\\*\\&]+?)\\s+([\\w_]+)\\s*\\(([\\s\\S]*?)\\)\\s*[;\\{]`,
        'g'
    );


    let funcMatch;
    while ((funcMatch = functionRegex.exec(content)) !== null) {
        let returnType = funcMatch[2].trim().replace(/\s+/g, ' ');
        const functionName = funcMatch[3].trim();
        let params = funcMatch[4].trim()
            .replace(/\/\/.*?\n/g, '')
            .replace(/\/\*[\s\S]*?\*\//g, '')
            .replace(/\s+/g, ' ')
            .trim();

        if (params === '' || params === 'void') {
            params = '无参数';
        }


        if (!functions.some(f => f.name === functionName)) {
            functions.push({ returnType, name: functionName, params });
        }
    }

    return functions;
}

function displayResults(functions) {
    resultBody.innerHTML = '';

    if (functions.length === 0) {
        resultBody.innerHTML = `
                    <tr>
                        <td colspan="4" class="empty-tip">未找到导出函数声明</td>
                    </tr>
                `;
    } else {
        functions.forEach((func, index) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                        <td>${index + 1}</td>
                        <td>${func.returnType}</td>
                        <td>${func.name}</td>
                        <td>${func.params}</td>
                    `;
            resultBody.appendChild(tr);
        });
    }

    resultArea.style.display = 'block';
}


exportBtn.addEventListener('click', () => {
    const rows = [];

    rows.push('序号,返回类型,函数名,参数列表');


    const trs = resultBody.querySelectorAll('tr');
    trs.forEach((tr, index) => {
        if (tr.querySelector('.empty-tip')) return;
        const tds = tr.querySelectorAll('td');
        const row = [
            tds[0].textContent,
            `"${tds[1].textContent}"`,
            `"${tds[2].textContent}"`,
            `"${tds[3].textContent}"`
        ].join(',');
        rows.push(row);
    });


    const csvContent = rows.join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `DLL导出函数分析_${new Date().getTime()}.csv`);
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
});


exportBtn2.addEventListener('click', () => {
    const funcLines = [];
    const trs = resultBody.querySelectorAll('tr');
    trs.forEach((tr) => {
        if (tr.querySelector('.empty-tip')) return;
        const tds = tr.querySelectorAll('td');
        if (tds.length < 4) return;
        let returnType = tds[1].textContent.trim().toLowerCase();
        returnType = returnType.replace(/[\*\&]/g, '').trim();
        let convertedReturnType = typeMap[returnType] || (returnType.includes('*') ? 'void' : returnType);
        const functionName = tds[2].textContent.trim();
        let params = tds[3].textContent.trim();
        if (params === '无参数') {
            params = '';
        } else {
            params = params.split(',').map(param => {
                let cleanedParam = param.trim().toLowerCase()
                    .replace(/\/\/.*$/, '') 
                    .replace(/const|volatile|static|extern/g, '') 
                    .replace(/[\*\&]/g, ' ') 
                    .replace(/\s+/g, ' ') 
                    .trim();
                const typePart = cleanedParam.split(' ')[0] || '';
                return typeMap[typePart] || 'void';
            }).join(',');
        }
        
        funcLines.push(`${functionName}|${params}|${convertedReturnType}`);
    });
    
    const funcContent = funcLines.join('\n');
    const blob = new Blob([funcContent], { type: 'text/plain;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `导出函数_${new Date().getTime()}.func`);
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
});

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(2) + ' KB';
    return (bytes / 1048576).toFixed(2) + ' MB';
}