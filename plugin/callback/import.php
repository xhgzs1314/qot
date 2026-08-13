<?php
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit();
}
$currentDir = dirname(__DIR__); 
$parentDir = dirname($currentDir);
define('PACKAGE_BASE', $parentDir . '\\package\\');


function jsonError($msg, $code = 400) {
    http_response_code($code);
    echo json_encode(['success' => false, 'error' => $msg], JSON_UNESCAPED_UNICODE);
    exit();
}

function jsonSuccess($data = []) {
    echo json_encode(array_merge(['success' => true], $data), JSON_UNESCAPED_UNICODE);
    exit();
}

function rrmdir($dir) {
    if (!is_dir($dir)) return;
    $items = scandir($dir);
    foreach ($items as $item) {
        if ($item === '.' || $item === '..') continue;
        $path = $dir . DIRECTORY_SEPARATOR . $item;
        is_dir($path) ? rrmdir($path) : unlink($path);
    }
    rmdir($dir);
}

try {
    $stdin = fopen('php://stdin', 'r');
    $rawInput = '';
    while (!feof($stdin)) {
        $rawInput .= fread($stdin, 1024);
    }
    fclose($stdin);
    if (empty($rawInput)) {
        jsonError('未收到数据 (php://input 为空)');
    }
    
    $input = json_decode($rawInput, true);
    
    if ($input === null) {
        jsonError('JSON 解析失败: ' . json_last_error_msg());
    }
    
    if (!isset($input['filename'])) {
        jsonError('缺少 filename 字段');
    }
    
    if (!isset($input['data'])) {
        jsonError('缺少 data 字段');
    }
    
    $filename = $input['filename'];
    $base64Data = $input['data'];
    $fileData = base64_decode($base64Data, true);
    
    if ($fileData === false) {
        jsonError('base64 解码失败');
    }
    
    // 检查文件大小
    if (strlen($fileData) > 20 * 1024 * 1024) {
        jsonError('文件过大，最大允许 20MB');
    }
    
    // 检查是否为 zip
    $ext = strtolower(pathinfo($filename, PATHINFO_EXTENSION));
    if ($ext !== 'zip') {
        jsonError('仅支持 .zip 格式');
    }
    
    // 创建临时目录
    $tempDir = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'qot_import_' . uniqid();
    if (!mkdir($tempDir, 0777, true)) {
        jsonError('无法创建临时目录');
    }
    
    // 保存
    $tempZip = $tempDir . DIRECTORY_SEPARATOR . 'module.zip';
    if (file_put_contents($tempZip, $fileData) === false) {
        rrmdir($tempDir);
        jsonError('无法保存上传文件');
    }
    $zip = new ZipArchive();
    if ($zip->open($tempZip) !== true) {
        rrmdir($tempDir);
        jsonError('无法打开 ZIP 文件');
    }
    $allowedExts = ['json', 'func', 'dll', 'txt'];
    $extractedFiles = [];
    for ($i = 0; $i < $zip->numFiles; $i++) {
        $stat = $zip->statIndex($i);
        $name = $stat['name'];
        if (strpos($name, '..') !== false || strpos($name, '/../') !== false) {
            $zip->close();
            rrmdir($tempDir);
            jsonError('ZIP 包含非法路径: ' . $name);
        }
        $ext = strtolower(pathinfo($name, PATHINFO_EXTENSION));
        if (!in_array($ext, $allowedExts)) {
            $zip->close();
            rrmdir($tempDir);
            jsonError('不允许的文件类型: ' . $ext);
        }
        $extractedFiles[] = $name;
    }
    
    if (!$zip->extractTo($tempDir)) {
        $zip->close();
        rrmdir($tempDir);
        jsonError('解压失败');
    }
    $zip->close();
    $manifestPath = $tempDir . DIRECTORY_SEPARATOR . 'manifest.json';
    if (!file_exists($manifestPath)) {
        rrmdir($tempDir);
        jsonError('缺少 manifest.json');
    }
    
    $manifestRaw = file_get_contents($manifestPath);
    $manifest = json_decode($manifestRaw, true);
    if ($manifest === null || !isset($manifest['m']['n'])) {
        rrmdir($tempDir);
        jsonError('manifest.json 格式无效或缺少模块名 (m.n)');
    }
    
    $moduleName = $manifest['m']['n'];
    if (empty($moduleName) || !preg_match('/^[a-zA-Z0-9_\-]+$/', $moduleName)) {
        rrmdir($tempDir);
        jsonError('模块名包含非法字符: ' . $moduleName);
    }
    $dllName = $moduleName . '.dll';
    $dllPath = $tempDir . DIRECTORY_SEPARATOR . $dllName;
    if (!file_exists($dllPath)) {
        rrmdir($tempDir);
        jsonError("缺少 {$dllName} (必须与 manifest 中的模块名一致)");
    }
    $funcPath = $tempDir . DIRECTORY_SEPARATOR . 'exports.func';
    if (!file_exists($funcPath)) {
        rrmdir($tempDir);
        jsonError('缺少 exports.func');
    }
    $targetDir = PACKAGE_BASE . $moduleName;
    if (is_dir($targetDir)) {
        rrmdir($targetDir);
    }
    if (!mkdir($targetDir, 0777, true)) {
        rrmdir($tempDir);
        jsonError('无法创建目标目录: ' . $targetDir);
    }
    
    $movedFiles = [];
    foreach (scandir($tempDir) as $item) {
        if ($item === '.' || $item === '..') continue;
        $ext = strtolower(pathinfo($item, PATHINFO_EXTENSION));
        if (!in_array($ext, $allowedExts)) continue;
        $src = $tempDir . DIRECTORY_SEPARATOR . $item;
        $dst = $targetDir . DIRECTORY_SEPARATOR . $item;
        if (is_file($src) && rename($src, $dst)) {
            $movedFiles[] = $item;
        }
    }
    
    rrmdir($tempDir);
    
    jsonSuccess([
        'module' => $moduleName,
        'path' => '/package/' . $moduleName . '/',
        'files' => $movedFiles,
        'manifest' => $manifest['m']
    ]);
    
} catch (Exception $e) {
    jsonError('服务器错误: ' . $e->getMessage(), 500);
}
?>