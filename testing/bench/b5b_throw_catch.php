<?php
// B5b 对照（PHP）：复用同一异常对象的抛接
$payload = new Exception("boom");
$acc = 0;
for ($i = 0; $i < 87654321; $i++) {
    try {
        throw $payload;
    } catch (Exception $e) {
        $acc = $acc + 1;
    }
}
echo $acc, "\n";
