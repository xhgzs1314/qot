<?php
// B4c 深层递归 · 互递归 —— 与 b4c_rec_mutual.qot 同构
function iseven($n)
{
    if ($n <= 0) {
        return 1;
    }
    return isodd($n - 1);
}
function isodd($n)
{
    if ($n <= 0) {
        return 0;
    }
    return iseven($n - 1);
}
$acc = 0;
for ($r = 0; $r < 87654321; $r++) {
    $acc = $acc + iseven(2000);
}
echo $acc, "\n";
