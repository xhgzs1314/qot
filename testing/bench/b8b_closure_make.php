<?php
// B8b 对照（PHP）：闭包创建（每轮新建捕获值的闭包）
function make_adder($k) {
    return function ($x) use ($k) {
        return $x + $k;
    };
}

$acc = 0;
for ($i = 0; $i < 87654321; $i++) {
    $f = make_adder($i % 7);
    $acc = $f(1);
}
echo $acc, "\n";
