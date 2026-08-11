.386
.model flat, C

.code

; double rpn_execute_asm(
;     const uint8_t* rpn_code,   ; [ESP+4]
;     uint32_t rpn_len,          ; [ESP+8]
;     const double* constants,   ; [ESP+12]
;     const double* variables,   ; [ESP+16]
;     uint32_t var_count         ; [ESP+20]
; )

rpn_execute_asm PROC
    push ebp
    mov ebp, esp
    sub esp, 64                  
    mov esi, [ebp+4]             ; esi = rpn_code
    mov ecx, [ebp+8]             ; ecx = rpn_len
    mov edi, [ebp+12]            ; edi = constants
    mov ebx, [ebp+16]            ; ebx = variables
    mov edx, [ebp+20]            ; edx = var_count
    
    ; 栈指针
    lea eax, [ebp-64]            ; eax = 栈底
    mov esp, eax                
    mov edi_org, edi
    mov ebx_org, ebx
    
    xor ecx, ecx                 ; 计数器 = 0
    
loop_start:
    cmp ecx, [ebp+8]             ; ecx < rpn_len?
    jge loop_end
    
    movzx eax, byte ptr [esi + ecx]  ; op
    inc ecx
    movzx edx, byte ptr [esi + ecx]  ; slot
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
    add eax, edi_org
    fld qword ptr [eax]
    fstp qword ptr [esp]
    sub esp, 8
    jmp loop_start

push_variable:
    cmp edx, [ebp+20]
    jae error_nan
    mov eax, edx
    shl eax, 3
    add eax, ebx_org
    fld qword ptr [eax]
    fstp qword ptr [esp]
    sub esp, 8
    jmp loop_start

op_add:
    fld qword ptr [esp]          ; right
    fld qword ptr [esp+8]        ; left
    faddp
    fstp qword ptr [esp+8]
    add esp, 8
    jmp loop_start

op_sub:
    fld qword ptr [esp]          ; right
    fld qword ptr [esp+8]        ; left
    fsubp
    fstp qword ptr [esp+8]
    add esp, 8
    jmp loop_start

op_mul:
    fld qword ptr [esp]          ; right
    fld qword ptr [esp+8]        ; left
    fmulp
    fstp qword ptr [esp+8]
    add esp, 8
    jmp loop_start

op_div:
    fld qword ptr [esp]          ; right
    fld qword ptr [esp+8]        ; left
    fdivp
    fstp qword ptr [esp+8]
    add esp, 8
    jmp loop_start

op_neg:
    fld qword ptr [esp]
    fchs
    fstp qword ptr [esp]
    jmp loop_start

loop_end:
    fld qword ptr [esp+8]        ; 结果
    jmp done

error_nan:
    fldz
    fdiv st(0), st(0)            ; NaN

done:
    mov esp, ebp
    pop ebp
    ret

rpn_execute_asm ENDP

END