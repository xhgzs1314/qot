.data

align 8
one_f64      dq 1.0
ten_f64      dq 10.0
zero_f64     dq 0.0
one_million  dq 1000000.0
log10_2      dq 0.30102999566398119521  
log10_e      dq 0.43429448190325182765  
exp_result   dq 0  



.code


DllMain proc
    mov eax, 1
    ret
DllMain endp

log10 PROC PRIVATE
    sub rsp, 8
    movsd qword ptr [rsp], xmm0
    
    fld qword ptr [rsp]
    fldlg2          ; log10(2)
    fxch
    fyl2x           ; ST(0) = log10(x)
    fstp qword ptr [rsp]
    movsd xmm0, qword ptr [rsp]
    
    add rsp, 8
    ret
log10 ENDP

; ============================================
; pow10 - 计算10的幂
; 参数: XMM0 = 指数
; 返回: XMM0 = 10^x
; ============================================
pow10 PROC PRIVATE
    sub rsp, 8
    movsd qword ptr [rsp], xmm0
    
    fld qword ptr [rsp]
    fldl2t          ; log2(10)
    fmulp           ; x * log2(10)
    f2xm1           ; 2^(x*log2(10)) - 1
    fld1
    faddp           ; 2^(x*log2(10))
    fstp qword ptr [rsp]
    movsd xmm0, qword ptr [rsp]
    
    add rsp, 8
    ret
pow10 ENDP
; ============================================
; fast_pow - 科学计数法快速幂（无溢出）
; 参数: RCX = 底数, RDX = 指数
; 返回: RAX = 编码后的科学计数法
;       高44位 = 指数, 低20位 = 小数部分 * 1e6
; ============================================
fast_pow PROC PUBLIC
    push rbx
    push rsi
    push rdi
    push rbp
    sub rsp, 32
    
    mov rbx, rcx                    ; rbx = base
    mov rsi, rdx                    ; rsi = exp
    
    ; 特殊值处理
    test rsi, rsi
    jz sci_exp_zero
    cmp rsi, 1
    je sci_exp_one
    cmp rbx, 0
    je sci_base_zero
    cmp rbx, 1
    je sci_base_one
    
    ; 使用对数方法: result = 10^(e * log10(base))
    cvtsi2sd xmm0, rbx
    call log10                      ; xmm0 = log10(base)
    cvtsi2sd xmm1, rsi
    mulsd xmm0, xmm1                ; xmm0 = e * log10(base)
    
    ; 分离指数和尾数
    cvttsd2si rdi, xmm0             ; 整数部分 = 指数偏移
    cvtsi2sd xmm1, rdi
    subsd xmm0, xmm1                ; 小数部分 (0-1之间)
    
    ; 计算 10^小数部分
    call pow10                      ; xmm0 = 10^小数部分 = 尾数 [1,10)
    
    ; 调整到尾数在[1,10)范围内
    mov rbp, rdi                    ; rbp = 指数偏移
    
    ; 归一化尾数到[1,10)
    movsd xmm1, xmm0
    xor rcx, rcx                    ; 额外的指数调整
    
norm_ge_10:
    comisd xmm1, qword ptr [ten_f64]
    jb norm_lt_1
    divsd xmm1, qword ptr [ten_f64]
    inc rcx
    jmp norm_ge_10
    
norm_lt_1:
    comisd xmm1, qword ptr [one_f64]
    jae norm_done
    mulsd xmm1, qword ptr [ten_f64]
    dec rcx
    jmp norm_lt_1
    
norm_done:
    movsd xmm0, xmm1
    add rbp, rcx                    ; 调整总指数
    
    ; 小数部分 * 1e6 取整
    movsd xmm1, qword ptr [one_million]
    mulsd xmm0, xmm1
    cvttsd2si rcx, xmm0
    
    ; 组合结果: 指数 << 20 | 小数编码
    mov rax, rbp
    shl rax, 20
    and rcx, 0xFFFFF
    or rax, rcx
    
    add rsp, 32
    pop rbp
    pop rdi
    pop rsi
    pop rbx
    ret

sci_exp_zero:
    mov rax, 1048576                ; 1.0 * 10^0
    add rsp, 32
    pop rbp
    pop rdi
    pop rsi
    pop rbx
    ret

sci_exp_one:
    cvtsi2sd xmm0, rbx
    mov rbp, 0
    jmp norm_done

sci_base_zero:
    xor rax, rax
    add rsp, 32
    pop rbp
    pop rdi
    pop rsi
    pop rbx
    ret

sci_base_one:
    mov rax, 1048576
    add rsp, 32
    pop rbp
    pop rdi
    pop rsi
    pop rbx
    ret

fast_pow ENDP

; ============================================
; fast_multiply - 快速乘法
; 参数: RCX = a, RDX = b
; 返回: RAX = a * b
; ============================================
fast_multiply PROC PUBLIC
    mov rax, rcx
    imul rax, rdx
    ret
fast_multiply ENDP

; ============================================
; fast_divide - 整数除法
; 参数: RCX = a, RDX = b
; 返回: RAX = a / b
; ============================================
fast_divide PROC PUBLIC
    test rdx, rdx
    jz div_by_zero
    mov rax, rcx
    cqo
    idiv rdx
    ret
    
div_by_zero:
    xor rax, rax
    mov rax, 7FFFFFFFFFFFFFFFh
    ret
fast_divide ENDP

; ============================================
; fast_mod - 快速取模
; 参数: RCX = a, RDX = b
; 返回: RAX = a % b
; ============================================
fast_mod PROC PUBLIC
    test rdx, rdx
    jz mod_zero
    mov rax, rcx
    cqo
    idiv rdx
    mov rax, rdx
    ret
    
mod_zero:
    xor rax, rax
    ret
fast_mod ENDP

; ============================================
; fast_abs - 快速绝对值
; 参数: RCX = 数值
; 返回: RAX = |数值|
; ============================================
fast_abs PROC PUBLIC
    mov rax, rcx
    test rax, rax
    jge abs_done
    neg rax
abs_done:
    ret
fast_abs ENDP

; ============================================
; fast_min - 最小值
; 参数: RCX = a, RDX = b
; 返回: RAX = min(a, b)
; ============================================
fast_min PROC PUBLIC
    cmp rcx, rdx
    jle min_done
    mov rcx, rdx
min_done:
    mov rax, rcx
    ret
fast_min ENDP

; ============================================
; fast_max - 最大值
; 参数: RCX = a, RDX = b
; 返回: RAX = max(a, b)
; ============================================
fast_max PROC PUBLIC
    cmp rcx, rdx
    jge max_done
    mov rcx, rdx
max_done:
    mov rax, rcx
    ret
fast_max ENDP

; ============================================
; fast_is_prime - 素数检测
; 参数: RCX = 数值
; 返回: RAX = 1(素数) / 0(不是)
; ============================================
fast_is_prime PROC PUBLIC
    cmp rcx, 2
    jl not_prime
    je is_prime
    
    test rcx, 1
    jz not_prime
    
    push rbx
    push rsi
    
    mov rbx, rcx        
    mov rsi, 3         
    mov rdx, rcx
    shr rdx, 1          
    
check_loop:
    cmp rsi, rdx
    jg prime_true
    
    mov rax, rbx
    xor rdx, rdx
    div rsi
    test rdx, rdx
    jz prime_false
    
    add rsi, 2
    jmp check_loop
    
prime_true:
    pop rsi
    pop rbx
    mov rax, 1
    ret
    
prime_false:
    pop rsi
    pop rbx
    xor rax, rax
    ret
    
not_prime:
    xor rax, rax
    ret
    
is_prime:
    mov rax, 1
    ret
fast_is_prime ENDP

; ============================================
; fast_factorial - 阶乘
; 参数: RCX = n
; 返回: RAX = n!
; ============================================
fast_factorial PROC PUBLIC
    cmp rcx, 1
    jle fac_one
    cmp rcx, 20
    jg fac_overflow
    
    push rbx
    mov rax, 1
    mov rbx, 2
    
fac_loop:
    imul rax, rbx
    inc rbx
    cmp rbx, rcx
    jle fac_loop
    
    pop rbx
    ret
    
fac_one:
    mov rax, 1
    ret
    
fac_overflow:
    mov rax, 0FFFFFFFFFFFFFFFFh
    ret
fast_factorial ENDP

; ============================================
; fast_fibonacci - 斐波那契数
; 参数: RCX = n
; 返回: RAX = Fib(n)
; ============================================
fast_fibonacci PROC PUBLIC
    cmp rcx, 2
    jl fib_base
    cmp rcx, 93
    jg fib_overflow
    
    push rbx
    xor rax, rax    ; F0 = 0
    mov rbx, 1      ; F1 = 1
    mov rdx, rcx
    
fib_loop:
    add rax, rbx
    xchg rax, rbx
    dec rdx
    cmp rdx, 1
    jg fib_loop
    
    mov rax, rbx
    pop rbx
    ret
    
fib_base:
    mov rax, rcx
    ret
    
fib_overflow:
    mov rax, 0FFFFFFFFFFFFFFFFh
    ret
fast_fibonacci ENDP

; ============================================
; fast_gcd - 最大公约数 (欧几里得算法)
; 参数: RCX = a, RDX = b
; 返回: RAX = GCD(a, b)
; ============================================
fast_gcd PROC PUBLIC
    test rdx, rdx
    jz gcd_done
    
    push rbx
    mov rax, rcx
    mov rbx, rdx
    
gcd_loop:
    xor rdx, rdx
    div rbx
    mov rax, rbx
    mov rbx, rdx
    test rbx, rbx
    jnz gcd_loop
    
    pop rbx
    ret
    
gcd_done:
    mov rax, rcx
    ret
fast_gcd ENDP

; ============================================
; fast_lcm - 最小公倍数
; 参数: RCX = a, RDX = b
; 返回: RAX = LCM(a, b)
; ============================================
fast_lcm PROC PUBLIC
    test rcx, rcx
    jz lcm_zero
    test rdx, rdx
    jz lcm_zero
    
    push rbx
    mov rbx, rcx
    imul rbx, rdx    ; a * b
    
    call fast_gcd
    xchg rax, rbx
    xor rdx, rdx
    div rbx
    
    pop rbx
    ret
    
lcm_zero:
    xor rax, rax
    ret
fast_lcm ENDP

; ============================================
; fast_power_mod - 模幂运算 (快速幂取模)
; 参数: RCX = base, RDX = exp, R8 = mod
; 返回: RAX = (base^exp) % mod
; ============================================
fast_power_mod PROC PUBLIC
    push rbx
    push rsi
    push rdi
    
    mov rax, 1          ; result = 1
    mov rbx, rcx        ; base
    mov rcx, rdx        ; exp
    mov rdi, r8         ; mod
    
    test rcx, rcx
    jz mod_done
    
mod_loop:
    test rcx, 1
    jz mod_skip_mul
    
    ; result = (result * base) % mod
    mov rsi, rax
    imul rsi, rbx
    mov rax, rsi
    xor rdx, rdx
    div rdi
    mov rax, rdx
    
mod_skip_mul:
    ; base = (base * base) % mod
    mov rsi, rbx
    imul rsi, rbx
    mov rbx, rsi
    xor rdx, rdx
    div rdi
    mov rbx, rdx
    
    shr rcx, 1
    test rcx, rcx
    jnz mod_loop
    
mod_done:
    pop rdi
    pop rsi
    pop rbx
    ret
fast_power_mod ENDP

; ============================================
; fast_floor_sqrt - 整数平方根 (牛顿迭代法)
; 参数: RCX = n
; 返回: RAX = floor(sqrt(n))
; ============================================
fast_floor_sqrt PROC PUBLIC
    test rcx, rcx
    jz sqrt_zero
    cmp rcx, 1
    je sqrt_one
    
    push rbx
    push rsi
    
    mov rax, rcx
    mov rbx, rcx
    shr rbx, 1          
    
sqrt_loop:
    mov rsi, rbx
    mov rax, rcx
    xor rdx, rdx
    div rbx
    add rax, rbx
    shr rax, 1
    mov rbx, rax
    cmp rax, rsi
    jl sqrt_loop
    
    mov rax, rsi
    
sqrt_done:
    pop rsi
    pop rbx
    ret
    
sqrt_zero:
    xor rax, rax
    ret
    
sqrt_one:
    mov rax, 1
    ret
fast_floor_sqrt ENDP

END