#include "rpn_executor.h"
#include "vm_types.h"
#include <cstring>
#include <iostream>

namespace qot {

bool RPNExecutor::compile_from_bytecode(
    const std::vector<std::pair<uint8_t, std::vector<uint8_t>>>& instructions,
    uint32_t start_ip,
    uint32_t end_ip,
    const std::vector<std::string>& var_names,
    const std::vector<Value>& constants,
    ExpressionInfo& out_info) {
    out_info.rpn_code.clear();
    out_info.constants.clear();
    out_info.var_indices.clear();
    std::vector<uint8_t> op_stack;
    std::vector<RPNInstruction> output;
    std::unordered_map<double, uint32_t> const_to_idx;
    for (size_t i = 0; i < constants.size(); i++) {
        if (constants[i].isNumber()) {
            double val = constants[i].asNumber();
            if (const_to_idx.find(val) == const_to_idx.end()) {
                const_to_idx[val] = static_cast<uint32_t>(out_info.constants.size());
                out_info.constants.push_back(val);
            }
        }
    }
    auto get_precedence = [](uint8_t op) -> int {
        if (op == 10 || op == 11) return 1;  
        if (op == 12 || op == 13) return 2;  
        return 0;
    };
    for (uint32_t ip = start_ip; ip < end_ip && ip < (uint32_t)instructions.size(); ip++) {
        auto& instr = instructions[ip];
        uint8_t op = instr.first;
        if (op == 1) {  
            int32_t idx = 0;
            if (instr.second.size() >= 4) {
                std::memcpy(&idx, instr.second.data(), 4);
            }
            if (idx >= 0 && idx < (int32_t)constants.size()) {
                const Value& v = constants[idx];
                if (v.isNumber()) {
                    RPNInstruction rpn;
                    rpn.op = 1;
                    rpn.slot = static_cast<uint8_t>(const_to_idx[v.asNumber()]);
                    output.push_back(rpn);
                }
            }
        }
        else if (op == 2) {  
            int32_t idx = 0;
            if (instr.second.size() >= 4) {
                std::memcpy(&idx, instr.second.data(), 4);
            }
            if (idx >= 0 && idx < (int32_t)var_names.size()) {
                RPNInstruction rpn;
                rpn.op = 2;
                rpn.slot = static_cast<uint8_t>(idx);
                output.push_back(rpn);
                out_info.var_indices.push_back(static_cast<uint32_t>(idx));
            }
        }
        else if (op == 10) { 
            int32_t binop = 0;
            if (instr.second.size() >= 4) {
                std::memcpy(&binop, instr.second.data(), 4);
            }
            uint8_t rpn_op = 0;
            switch (binop) {
                case 1: rpn_op = 10; break;  // ADD
                case 2: rpn_op = 11; break;  // SUB
                case 3: rpn_op = 12; break;  // MUL
                case 4: rpn_op = 13; break;  // DIV
                default: continue;
            }
            while (!op_stack.empty() && get_precedence(op_stack.back()) >= get_precedence(rpn_op)) {
                RPNInstruction rpn;
                rpn.op = op_stack.back();
                rpn.slot = 0;
                output.push_back(rpn);
                op_stack.pop_back();
            }
            op_stack.push_back(rpn_op);
        }
        else if (op == 11) {  // UNARY_OP
            int32_t unop = 0;
            if (instr.second.size() >= 4) {
                std::memcpy(&unop, instr.second.data(), 4);
            }
            if (unop == 1) {  // NEG
                op_stack.push_back(14);
            }
        }
    }
    while (!op_stack.empty()) {
        RPNInstruction rpn;
        rpn.op = op_stack.back();
        rpn.slot = 0;
        output.push_back(rpn);
        op_stack.pop_back();
    }
    std::sort(out_info.var_indices.begin(), out_info.var_indices.end());
    out_info.var_indices.erase(
        std::unique(out_info.var_indices.begin(), out_info.var_indices.end()),
        out_info.var_indices.end()
    );
    out_info.var_count = static_cast<uint32_t>(out_info.var_indices.size());
    
    out_info.rpn_code = output;
    out_info.hash = compute_hash(out_info);
    
    return !output.empty();
}

uint32_t RPNExecutor::compute_hash(const ExpressionInfo& info) {
    uint32_t hash = 0x811C9DC5;
    for (const auto& instr : info.rpn_code) {
        hash ^= instr.op;
        hash ^= instr.slot;
        hash *= 0x01000193;
    }
    return hash;
}


static double rpn_execute_cpp(
    const RPNInstruction* rpn_code,
    uint32_t rpn_len,
    const double* constants,
    const double* variables,
    uint32_t var_count) {
    
    double stack[128];
    double* sp = stack;
    
    for (uint32_t i = 0; i < rpn_len; i++) {
        uint8_t op = rpn_code[i].op;
        uint8_t slot = rpn_code[i].slot;
        
        switch (op) {
            case 1:  // CONSTANT
                *sp++ = constants[slot];
                break;
            case 2:  // VARIABLE
                *sp++ = (slot < var_count) ? variables[slot] : 0.0;
                break;
            case 10: // ADD
                sp--;
                sp[-1] = sp[-1] + sp[0];
                break;
            case 11: // SUB
                sp--;
                sp[-1] = sp[-1] - sp[0];
                break;
            case 12: // MUL
                sp--;
                sp[-1] = sp[-1] * sp[0];
                break;
            case 13: // DIV
                sp--;
                if (sp[0] != 0.0) {
                    sp[-1] = sp[-1] / sp[0];
                } else {
                    sp[-1] = 0.0;
                }
                break;
            case 14: // NEG
                sp[-1] = -sp[-1];
                break;
        }
    }
    
    return sp[-1];
}

#ifdef _M_X64
extern "C" double rpn_execute_asm(
    const uint8_t* rpn_code,
    uint32_t rpn_len,
    const double* constants,
    const double* variables,
    uint32_t var_count);
#endif

double RPNExecutor::execute(
    const RPNInstruction* rpn_code,
    uint32_t rpn_len,
    const double* constants,
    const double* variables,
    uint32_t var_count) {
    
#ifdef _M_X64
    return rpn_execute_asm(
        reinterpret_cast<const uint8_t*>(rpn_code),
        rpn_len,
        constants,
        variables,
        var_count);
#else
    return rpn_execute_cpp(rpn_code, rpn_len, constants, variables, var_count);
#endif
}

} 