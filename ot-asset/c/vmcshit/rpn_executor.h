#ifndef RPN_EXECUTOR_H
#define RPN_EXECUTOR_H

#include <cstdint>
#include <vector>
#include <string>
#include <unordered_map>
#include <algorithm>

namespace qot {
    class Value;
}

namespace qot {
struct RPNInstruction {
    uint8_t op;     
    uint8_t slot;    
};
struct ExpressionInfo {
    std::vector<RPNInstruction> rpn_code;
    std::vector<double> constants;
    std::vector<uint32_t> var_indices;
    uint32_t var_count = 0;
    uint32_t hash = 0;
};
class RPNExecutor {
public:
    static bool compile_from_bytecode(
        const std::vector<std::pair<uint8_t, std::vector<uint8_t>>>& instructions,
        uint32_t start_ip,
        uint32_t end_ip,
        const std::vector<std::string>& var_names,
        const std::vector<Value>& constants,
        ExpressionInfo& out_info
    );
    
    static double execute(
        const RPNInstruction* rpn_code,
        uint32_t rpn_len,
        const double* constants,
        const double* variables,
        uint32_t var_count
    );
    
    static uint32_t compute_hash(const ExpressionInfo& info);
};

} 

#endif