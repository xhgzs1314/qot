<?php
function discount($level, $price) {
    if ($level == 1) { return $price * 0.95; }
    if ($level == 2) { return $price * 0.90; }
    if ($level == 3) { return $price * 0.85; }
    if ($level == 4) { return $price * 0.80; }
    return $price * 0.70;
}
function classify($amount) {
    if ($amount > 1000) { return 3; }
    if ($amount > 500) { return 2; }
    if ($amount > 100) { return 1; }
    return 0;
}
$buckets = [0, 0, 0, 0];
$total = 0.0;
for ($order = 0; $order < 87654321; $order++) {
    $level = $order % 5 + 1;
    $qty = $order % 9 + 1;
    $price = ($order % 977) * 1.5 + 10;
    $d = discount($level, $price);
    $amount = $d * $qty * 1.13;
    $cat = classify($amount);
    $buckets[$cat] = $buckets[$cat] + 1;
    $total = $total + $amount;
}
echo $total, "\n";
echo $buckets[0], "\n";
echo $buckets[1], "\n";
echo $buckets[2], "\n";
echo $buckets[3], "\n";
