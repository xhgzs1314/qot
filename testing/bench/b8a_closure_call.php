<?php
// B8a 对照（PHP）：闭包调用 + use(&$n) 引用捕获读写
function make_counter() {
    $n = 0;
    return function ($dummy) use (&$n) {
        $n = $n + 1;
        return $n;
    };
}

$step = make_counter();
$acc = 0;
for ($i = 0; $i < 87654321; $i++) {
    $acc = $step($i);
}
echo $acc, "\n";
