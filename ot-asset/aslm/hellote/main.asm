extern MessageBoxA:proc
extern ExitProcess:proc

.data
    msg db "山海永赴,我爱你亘古不变...", 0
    msg_title db "此心不渝！", 0

.code
start proc
    sub rsp, 28h
    mov r9d, 0
    lea r8, msg_title
    lea rdx, msg
    mov rcx, 0
    call MessageBoxA
    mov rcx, rax
    call ExitProcess
start endp
end