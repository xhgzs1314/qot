// 本文件由 _gen_builtins.py 从 builtins.toml 生成，**不要手改**。
// 改内置请改 builtins.toml，然后重跑 `python _gen_builtins.py`。
//
// M1 / G-5：VM 侧的内置名单不再是独立维护的一份表。清单说有而 C++ 侧
// 没有对应函数 → 展开时符号未声明 → 编译失败（不会静默少注册一个）。
#ifndef QOT_BUILTINS_MANIFEST_H
#define QOT_BUILTINS_MANIFEST_H

// X(内置名, C++ 函数符号)。别名（len/trim/...）也各占一行、指向同一个
// 函数：别名是语言层名字，只在一侧登记就是新的 G-5 分歧。
#define QOT_VM_BUILTINS(X) \
    X(output, builtin_output) \
    X(cinget, builtin_input) \
    X(glen, builtin_len) \
    X(strings, builtin_to_string) \
    X(push, builtin_push) \
    X(remove, builtin_remove) \
    X(pop, builtin_pop) \
    X(get, builtin_get_index) \
    X(fopen, builtin_fopen) \
    X(fread, builtin_fread) \
    X(fclose, builtin_fclose) \
    X(fwrite, builtin_fwrite) \
    X(unlink, builtin_unlink) \
    X(fnew, builtin_fnew) \
    X(sleep, builtin_sleep) \
    X(gtypes, builtin_gtypes) \
    X(tnow, builtin_tnow) \
    X(tstamp, builtin_tstamp) \
    X(fsize, builtin_fsize) \
    X(sqrt, builtin_sqrt) \
    X(rand, builtin_rand) \
    X(strcase, builtin_strcase) \
    X(strrep, builtin_str_replace) \
    X(pow, builtin_pow) \
    X(round, builtin_round) \
    X(dict_get, builtin_dict_get) \
    X(dict_set, builtin_dict_set) \
    X(dict_keys, builtin_dict_keys) \
    X(http_send, builtin_http_send) \
    X(cmd_exec, builtin_cmd_exec) \
    X(ScLoad, builtin_scload) \
    X(ScExec, builtin_scexec) \
    X(ScSweep, builtin_scsweep) \
    X(set_env, builtin_set_env) \
    X(get_env, builtin_get_env) \
    X(unset_env, builtin_unset_env) \
    X(__is_number, builtin_is_number) \
    X(__iter_len, builtin_iter_len) \
    X(json_encode, builtin_json_encode) \
    X(json_decode, builtin_json_decode) \
    X(type, builtin_type) \
    X(numbers, builtin_to_number) \
    X(http_set_header, builtin_http_set_header) \
    X(http_set_user_agent, builtin_http_set_user_agent) \
    X(http_set_verify_ssl, builtin_http_set_verify_ssl) \
    X(http_clear_headers, builtin_http_clear_headers) \
    X(http_get_headers, builtin_http_get_headers) \
    X(input, builtin_input) \
    X(len, builtin_len) \
    X(str, builtin_to_string) \
    X(replace, builtin_str_replace)

#define QOT_VM_BUILTIN_COUNT 51

#endif  // QOT_BUILTINS_MANIFEST_H
