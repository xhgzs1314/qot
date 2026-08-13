<?php
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit();
}
$currentDir = dirname(__DIR__);
$parentDir = dirname($currentDir);
define('PACKAGE_DIR', $parentDir . '\\package\\');
function scanManifests($dir)
{
    $results = [];
    if (!is_dir($dir)) {
        return $results;
    }
    $items = scandir($dir);
    foreach ($items as $item) {
        if ($item === '.' || $item === '..') {
            continue;
        }
        $fullPath = $dir . DIRECTORY_SEPARATOR . $item;
        if (is_dir($fullPath)) {
            $manifestPath = $fullPath . DIRECTORY_SEPARATOR . 'manifest.json';
            if (is_file($manifestPath)) {
                $manifestData = json_decode(file_get_contents($manifestPath), true);
                if ($manifestData !== null && isset($manifestData['m'])) {
                    $manifestData['_dir'] = $item;
                    $results[] = $manifestData;
                }
            }
        }
    }

    return $results;
}
function getModuleByName($moduleName)
{
    $manifestPath = PACKAGE_DIR . $moduleName . DIRECTORY_SEPARATOR . 'manifest.json';

    if (!is_file($manifestPath)) {
        return null;
    }

    $manifestData = json_decode(file_get_contents($manifestPath), true);

    if ($manifestData === null || !isset($manifestData['m'])) {
        return null;
    }

    $manifestData['_dir'] = $moduleName;
    return $manifestData;
}
function normalizeModules($manifests)
{
    $modules = [];

    foreach ($manifests as $manifest) {
        if (isset($manifest['m']) && is_array($manifest['m'])) {
            $module = $manifest['m'];

            // 模块名
            if (!isset($module['n'])) {
                $module['n'] = $manifest['_dir'] ?? 'unknown';
            }

            // 描述
            if (!isset($module['d'])) {
                $module['d'] = '';
            }

            // 根层级
            if (!isset($module['author']) && isset($manifest['author'])) {
                $module['author'] = $manifest['author'];
            }

            if (!isset($module['gp']) && isset($manifest['gp'])) {
                $module['gp'] = $manifest['gp'];
            }

            if (!isset($module['ct']) && isset($manifest['ct'])) {
                $module['ct'] = $manifest['ct'];
            }

            // 方法列表
            if (!isset($module['f']) || !is_array($module['f'])) {
                $module['f'] = [];
            }

            foreach ($module['f'] as &$func) {
                if (!isset($func['n'])) $func['n'] = 'anonymous';
                if (!isset($func['d'])) $func['d'] = '';
                if (!isset($func['p'])) $func['p'] = 'void';
                if (!isset($func['r'])) $func['r'] = 'void';
                if (!isset($func['de'])) $func['de'] = [];
            }

            $modules[] = $module;
        }
    }

    return $modules;
}
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
if ($input) {
    $action = $input['action'] ?? '';
    $moduleName = $input['module'] ?? '';
    $keyword = $input['keyword'] ?? '';
} else {
    $action = $_GET['action'] ?? '';
    $moduleName = $_GET['module'] ?? '';
    $keyword = $_GET['keyword'] ?? '';
}

try {
    switch ($action) {
        case 'list':
            $manifests = scanManifests(PACKAGE_DIR);
            $modules = normalizeModules($manifests);
            echo json_encode([
                'success' => true,
                'count' => count($modules),
                'data' => $modules,
                'path' => PACKAGE_DIR
            ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
            break;
        case 'delete':
            if (empty($moduleName)) {
                throw new Exception('缺少 module 参数');
            }

            $targetDir = PACKAGE_DIR . $moduleName;

            if (!is_dir($targetDir)) {
                throw new Exception('模块不存在: ' . $moduleName);
            }


            function rrmdir($dir)
            {
                if (!is_dir($dir)) return;
                $items = scandir($dir);
                foreach ($items as $item) {
                    if ($item === '.' || $item === '..') continue;
                    $path = $dir . DIRECTORY_SEPARATOR . $item;
                    is_dir($path) ? rrmdir($path) : unlink($path);
                }
                rmdir($dir);
            }

            rrmdir($targetDir);

            echo json_encode([
                'success' => true,
                'message' => '模块已删除: ' . $moduleName . ')' . $targetDir
            ], JSON_UNESCAPED_UNICODE);
            break;
        case 'get':
            if (empty($moduleName)) {
                throw new Exception('缺少 module 参数');
            }

            $manifest = getModuleByName($moduleName);

            if ($manifest === null) {
                throw new Exception('模块不存在: ' . $moduleName);
            }

            $modules = normalizeModules([$manifest]);

            echo json_encode([
                'success' => true,
                'data' => $modules[0] ?? null
            ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
            break;

        case 'search':
            $keyword = $_GET['keyword'] ?? '';
            if (empty($keyword)) {
                $manifests = scanManifests(PACKAGE_DIR);
            } else {
                $allManifests = scanManifests(PACKAGE_DIR);
                $manifests = array_filter($allManifests, function ($manifest) use ($keyword) {
                    $moduleName = $manifest['m']['n'] ?? '';
                    $moduleDesc = $manifest['m']['d'] ?? '';

                    return stripos($moduleName, $keyword) !== false ||
                        stripos($moduleDesc, $keyword) !== false;
                });
            }

            $modules = normalizeModules($manifests);

            echo json_encode([
                'success' => true,
                'count' => count($modules),
                'data' => $modules
            ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
            break;

        default:
            throw new Exception('未知操作: ' . $action);
    }
} catch (Exception $e) {
    http_response_code(400);
    echo json_encode([
        'success' => false,
        'error' => $e->getMessage()
    ], JSON_UNESCAPED_UNICODE);
}
