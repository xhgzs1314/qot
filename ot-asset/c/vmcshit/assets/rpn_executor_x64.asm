.code

; double rpn_execute_asm(
;     const uint8_t* rpn_code,   ; RCX
;     uint32_t rpn_len,          ; EDX
;     const double* constants,   ; R8
;     const double* variables,   ; R9
;     uint32_t var_count         ; [RSP+28h]
; )

rpn_execute_asm PROC
    push rbp
    push rbx
    push rsi
    push rdi
    push r12
    push r13
    push r14
    push r15
    
    mov rbp, rsp
    sub rsp, 128
    
    mov rbx, rcx                 ; rbx = rpn_code
    mov esi, edx                 ; esi = rpn_len
    mov rdi, r8                  ; rdi = constants
    mov r12, r9                  ; r12 = variables
    mov r13d, dword ptr [rbp+48] ; r13d = var_count
    
    lea r14, [rsp]               ; r14 = 栈顶
    xor ecx, ecx                 ; 计数器
    
loop_start:
    cmp ecx, esi
    jge loop_end
    
    movzx eax, byte ptr [rbx + rcx*2]     ; op
    movzx edx, byte ptr [rbx + rcx*2 + 1] ; slot
    inc ecx
    
    cmp al, 1
    je push_constant
    cmp al, 2
    je push_variable
    cmp al, 10
    je op_add
    cmp al, 11
    je op_sub
    cmp al, 12
    je op_mul
    cmp al, 13
    je op_div
    cmp al, 14
    je op_neg
    
    jmp loop_start

push_constant:
    mov eax, edx
    shl eax, 3
    movsd xmm0, qword ptr [rdi + rax]
    movsd qword ptr [r14], xmm0
    add r14, 8
    jmp loop_start

push_variable:
    cmp edx, r13d
    jae push_zero
    mov eax, edx
    shl eax, 3
    movsd xmm0, qword ptr [r12 + rax]
    movsd qword ptr [r14], xmm0
    add r14, 8
    jmp loop_start
push_zero:
    xorps xmm0, xmm0
    movsd qword ptr [r14], xmm0
    add r14, 8
    jmp loop_start

op_add:
    sub r14, 16
    movsd xmm0, qword ptr [r14]
    movsd xmm1, qword ptr [r14 + 8]
    addsd xmm0, xmm1
    movsd qword ptr [r14], xmm0
    add r14, 8
    jmp loop_start

op_sub:
    sub r14, 16
    movsd xmm0, qword ptr [r14]
    movsd xmm1, qword ptr [r14 + 8]
    subsd xmm0, xmm1
    movsd qword ptr [r14], xmm0
    add r14, 8
    jmp loop_start

op_mul:
    sub r14, 16
    movsd xmm0, qword ptr [r14]
    movsd xmm1, qword ptr [r14 + 8]
    mulsd xmm0, xmm1
    movsd qword ptr [r14], xmm0
    add r14, 8
    jmp loop_start

op_div:
    sub r14, 16
    movsd xmm0, qword ptr [r14]
    movsd xmm1, qword ptr [r14 + 8]
    divsd xmm0, xmm1
    movsd qword ptr [r14], xmm0
    add r14, 8
    jmp loop_start

op_neg:
    sub r14, 8
    movsd xmm0, qword ptr [r14]
    xorps xmm1, xmm1
    subsd xmm1, xmm0
    movsd qword ptr [r14], xmm1
    add r14, 8
    jmp loop_start

loop_end:
    sub r14, 8
    movsd xmm0, qword ptr [r14]
    
    add rsp, 128
    pop r15
    pop r14
    pop r13
    pop r12
    pop rdi
    pop rsi
    pop rbx
    pop rbp
    ret

rpn_execute_asm ENDP

END