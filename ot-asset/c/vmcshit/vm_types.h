#ifndef VM_TYPES_H
#define VM_TYPES_H

#include <vector>
#include <string>
#include <unordered_map>
#include <variant>
#include <memory>
#include <functional>
#include <stack>
#include <cstdint>
#include <list>
#include <stdexcept>
#include <new>
#include <utility>
#if defined(_MSC_VER)
#define QOT_INLINE __forceinline
#define QOT_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define QOT_INLINE inline __attribute__((always_inline))
#define QOT_NOINLINE __attribute__((noinline))
#else
#define QOT_INLINE inline
#define QOT_NOINLINE
#endif

namespace qot
{
    static const int32_t GLOBAL_BIT = 0x40000000;
    enum class OpCode : uint8_t
    {
        NOP = 0,
        PUSH_CONST = 1,
        PUSH_VAR = 2,
        STORE_VAR = 3,
        STORE_CONST = 4,
        POP = 5,
        POP_STMT = 50,
        BINARY_OP = 10,
        UNARY_OP = 11,
        JUMP = 20,
        JUMP_IF_FALSE = 21,
        JUMP_IF_TRUE = 22,
        CALL = 30,
        CALL_METHOD = 31,
        CALL_STATIC = 33,
        RETURN = 32,
        MAKE_ARRAY = 40,
        MAKE_DICT = 41,
        INDEX_GET = 42,
        INDEX_SET = 43,
        STOP = 51,
        REQUIRE = 60,
        BREAK = 52,
        RPN_LOOP_START = 200,
        RPN_LOOP_END = 201,
    };

    enum class BinaryOp : uint8_t
    {
        ADD = 1,
        SUB = 2,
        MUL = 3,
        DIV = 4,
        MOD = 5,
        POW = 6,
        GT = 7,
        LT = 8,
        GE = 9,
        LE = 10,
        EQ = 11,
        NE = 12,
        AND = 13,
        OR = 14
    };

    enum class UnaryOp : uint8_t
    {
        NEG = 1,
        NOT = 2
    };

    class Value;
    struct CodeBlock;

    using FunctionType = std::function<Value(const std::vector<Value> &)>;
    using BuiltinFunction = std::function<Value(const std::vector<Value> &)>;
    struct ObjHeader
    {
        uint32_t rc;
        explicit ObjHeader(uint32_t n) : rc(n) {}
    };

    template <class T>
    struct Box : ObjHeader
    {
        T obj;
        template <class... A>
        explicit Box(A &&...a) : ObjHeader(1), obj(std::forward<A>(a)...) {}
    };

   
    template <class T>
    class Ref
    {
        Box<T> *m_p = nullptr;

    public:
        Ref() = default;
        Ref(std::nullptr_t) {}
        explicit Ref(Box<T> *p) : m_p(p) {} // 接管所有权
        Ref(const Ref &o) : m_p(o.m_p)
        {
            if (m_p)
                ++m_p->rc;
        }
        Ref(Ref &&o) noexcept : m_p(o.m_p) { o.m_p = nullptr; }
        ~Ref() { reset(); }

        Ref &operator=(const Ref &o)
        {
            if (this != &o)
            {
                if (o.m_p)
                    ++o.m_p->rc; // 先增后减
                reset();
                m_p = o.m_p;
            }
            return *this;
        }
        Ref &operator=(Ref &&o) noexcept
        {
            if (this != &o)
            {
                reset();
                m_p = o.m_p;
                o.m_p = nullptr;
            }
            return *this;
        }
        Ref &operator=(std::nullptr_t)
        {
            reset();
            return *this;
        }

        void reset()
        {
            if (m_p && --m_p->rc == 0)
                delete m_p;
            m_p = nullptr;
        }

        T *get() const { return m_p ? &m_p->obj : nullptr; }
        T &operator*() const { return m_p->obj; }
        T *operator->() const { return &m_p->obj; }
        explicit operator bool() const { return m_p != nullptr; }
        bool operator==(std::nullptr_t) const { return m_p == nullptr; }
        bool operator!=(std::nullptr_t) const { return m_p != nullptr; }
        bool operator==(const Ref &o) const { return m_p == o.m_p; }
        bool operator!=(const Ref &o) const { return m_p != o.m_p; }
        Box<T> *box() const { return m_p; }

        template <class... A>
        static Ref make(A &&...a) { return Ref(new Box<T>(std::forward<A>(a)...)); }
    };

    using ArrayPtr = Ref<std::vector<Value>>;
    using DictPtr = Ref<std::unordered_map<std::string, Value>>;
    using CodeBlockPtr = Ref<CodeBlock>;


    struct Instr
    {
        OpCode op;
        uint8_t argLen; // 截断到 255
        int32_t blobIdx;
        int32_t a;
        int32_t b;
    };

    struct BreakInfo
    {
        size_t targetIp;
        size_t loopEndIp;

        BreakInfo(size_t target) : targetIp(target), loopEndIp(0) {}
        BreakInfo(size_t target, size_t end) : targetIp(target), loopEndIp(end) {}
    };

    class BreakException : public std::exception
    {
    public:
        const char *what() const noexcept override
        {
            return "Break statement executed";
        }
    };

   
    class Value
    {
    public:
        enum class Type : uint8_t
        {
            NONE,
            NULL_,
            BOOL,
            NUMBER,
            STRING,
            ARRAY,
            DICT,
            FUNCTION,
            BUILTIN,
            CODEBLOCK
        };

    private:
        Type m_type;
        union
        {
            uint64_t m_bits;
            bool m_bool;
            double m_num;
            ObjHeader *m_obj;
        };

        static QOT_INLINE bool isHeapType(Type t)
        {
            return t == Type::STRING || t == Type::ARRAY || t == Type::DICT ||
                   t == Type::FUNCTION || t == Type::CODEBLOCK;
        }
        void destroy(); // 需要 CodeBlock
        QOT_INLINE void retain() const
        {
            if (isHeapType(m_type))
                ++m_obj->rc;
        }
        QOT_INLINE void release()
        {
            if (isHeapType(m_type) && --m_obj->rc == 0)
                destroy();
        }

    public:
        Value() : m_type(Type::NONE), m_bits(0) {}
        Value(std::nullptr_t) : m_type(Type::NULL_), m_bits(0) {}
        Value(bool v) : m_type(Type::BOOL), m_bits(0) { m_bool = v; }
        Value(int v) : m_type(Type::NUMBER), m_bits(0) { m_num = static_cast<double>(v); }
        Value(double v) : m_type(Type::NUMBER), m_bits(0) { m_num = v; }
        Value(const char *v);
        Value(const std::string &v);
        Value(std::string &&v);
        Value(const std::vector<Value> &v);
        Value(ArrayPtr ptr);
        Value(const std::unordered_map<std::string, Value> &v);
        Value(DictPtr ptr);
        Value(const FunctionType &v);
        Value(CodeBlockPtr ptr);
        Value(const CodeBlock &v);

        QOT_INLINE Value(const Value &o) : m_type(o.m_type)
        {
            m_bits = o.m_bits;
            retain();
        }
        QOT_INLINE Value(Value &&o) noexcept : m_type(o.m_type)
        {
            m_bits = o.m_bits;
            o.m_type = Type::NONE;
            o.m_bits = 0;
        }
        QOT_INLINE Value &operator=(const Value &o)
        {
            if (this != &o)
            {
                o.retain(); // 先增后减
                release();
                m_type = o.m_type;
                m_bits = o.m_bits;
            }
            return *this;
        }
        QOT_INLINE Value &operator=(Value &&o) noexcept
        {
            if (this != &o)
            {
                release();
                m_type = o.m_type;
                m_bits = o.m_bits;
                o.m_type = Type::NONE;
                o.m_bits = 0;
            }
            return *this;
        }
        QOT_INLINE ~Value() { release(); }

        Type type() const { return m_type; }
        bool isNone() const { return m_type == Type::NONE; }
        bool isNull() const { return m_type == Type::NULL_; }
        bool isBool() const { return m_type == Type::BOOL; }
        bool isNumber() const { return m_type == Type::NUMBER; }
        bool isString() const { return m_type == Type::STRING; }
        bool isArray() const { return m_type == Type::ARRAY; }
        bool isDict() const { return m_type == Type::DICT; }
        bool isFunction() const { return m_type == Type::FUNCTION; }
        bool isBuiltin() const { return m_type == Type::BUILTIN; }
        bool isCodeBlock() const { return m_type == Type::CODEBLOCK; }

        static Value createArray();
        static Value createDict();

        bool asBool() const
        {
            if (m_type != Type::BOOL)
                throw std::runtime_error("not a bool");
            return m_bool;
        }

        double asNumber() const
        {
            if (m_type != Type::NUMBER)
                throw std::runtime_error("not a number");
            return m_num;
        }

        // 热路径专用
        double rawNumber() const { return m_num; }
        bool rawBool() const { return m_bool; }
        const std::string &asString() const;
        std::string &asString();
        std::vector<Value> &asArray();
        const std::vector<Value> &asArray() const;
        std::unordered_map<std::string, Value> &asDict();
        const std::unordered_map<std::string, Value> &asDict() const;
        const FunctionType &asFunction() const;
        CodeBlockPtr asCodeBlock() const;
        const void *getPtr() const;
        QOT_INLINE bool isTruthy() const
        {
            switch (m_type)
            {
            case Type::NONE:
            case Type::NULL_:
                return false;
            case Type::BOOL:
                return m_bool;
            case Type::NUMBER:
                return m_num != 0;
            default:
                return isTruthySlow();
            }
        }
        bool isTruthySlow() const;
        std::string toString() const;
    };

    
  
    class OperandStack
    {
        Value *m_data = nullptr;
        Value *m_top = nullptr;
        Value *m_end = nullptr;
        QOT_NOINLINE void grow()
        {
            size_t oldSize = static_cast<size_t>(m_top - m_data);
            size_t nc = m_data ? static_cast<size_t>(m_end - m_data) * 2 : 64;
            Value *nd = static_cast<Value *>(::operator new(sizeof(Value) * nc));
            for (size_t i = 0; i < oldSize; i++)
            {
                new (nd + i) Value(std::move(m_data[i]));
                m_data[i].~Value();
            }
            ::operator delete(m_data);
            m_data = nd;
            m_top = nd + oldSize;
            m_end = nd + nc;
        }

        void destroyAll()
        {
            while (m_top != m_data)
                (--m_top)->~Value();
        }

    public:
        OperandStack() = default;
        ~OperandStack()
        {
            destroyAll();
            ::operator delete(m_data);
        }
        OperandStack(const OperandStack &o)
        {
            size_t n = o.size();
            if (n)
            {
                m_data = static_cast<Value *>(::operator new(sizeof(Value) * n));
                m_end = m_data + n;
                for (size_t i = 0; i < n; i++)
                    new (m_data + i) Value(o.m_data[i]);
                m_top = m_data + n;
            }
        }
        OperandStack(OperandStack &&o) noexcept
            : m_data(o.m_data), m_top(o.m_top), m_end(o.m_end)
        {
            o.m_data = o.m_top = o.m_end = nullptr;
        }
        OperandStack &operator=(const OperandStack &o)
        {
            if (this != &o)
            {
                OperandStack tmp(o);
                *this = std::move(tmp);
            }
            return *this;
        }
        OperandStack &operator=(OperandStack &&o) noexcept
        {
            if (this != &o)
            {
                destroyAll();
                ::operator delete(m_data);
                m_data = o.m_data;
                m_top = o.m_top;
                m_end = o.m_end;
                o.m_data = o.m_top = o.m_end = nullptr;
            }
            return *this;
        }

        QOT_INLINE bool empty() const { return m_top == m_data; }
        QOT_INLINE size_t size() const { return static_cast<size_t>(m_top - m_data); }
        QOT_INLINE Value &back() { return m_top[-1]; }
        QOT_INLINE const Value &back() const { return m_top[-1]; }
        QOT_INLINE Value &operator[](size_t i) { return m_data[i]; }
        QOT_INLINE const Value &operator[](size_t i) const { return m_data[i]; }
        QOT_INLINE void pop_back() { (--m_top)->~Value(); }
        void clear() { destroyAll(); }

        QOT_INLINE void push_back(const Value &v)
        {
            if (m_top == m_end)
                grow();
            new (m_top++) Value(v);
        }
        QOT_INLINE void push_back(Value &&v)
        {
            if (m_top == m_end)
                grow();
            new (m_top++) Value(std::move(v));
        }
        template <class... A>
        QOT_INLINE void emplace_back(A &&...a)
        {
            if (m_top == m_end)
                grow();
            new (m_top++) Value(std::forward<A>(a)...);
        }
    };

    struct StackFrame
    {
        OperandStack stack;
        std::vector<Value> locals;
        std::vector<Value> constants;
        size_t ip = 0;                      
        size_t returnIp = 0;                
        std::string functionName;           
        bool isConst = false;
        void clear()
        {
            stack.clear();
            locals.clear();
            constants.clear();
            ip = 0;
            returnIp = 0;
            functionName.clear();
            isConst = false;
        }
        void reuse()
        {
            stack.clear();
            locals.clear();
            constants.clear();
            ip = 0;
            returnIp = 0;
            functionName.clear();
            isConst = false;
        }

        void resize(size_t local_count, size_t const_count)
        {
            locals.resize(local_count);
            constants.resize(const_count);
        }
        
        void set_local(uint32_t idx, const Value& v)
        {
            if (idx < locals.size())
                locals[idx] = v;
        }
        
        Value& get_local(uint32_t idx)
        {
            return locals[idx];
        }
        
        const Value& get_local(uint32_t idx) const
        {
            return locals[idx];
        }
        
        void set_const(uint32_t idx, const Value& v)
        {
            if (idx < constants.size())
                constants[idx] = v;
        }
        
        Value& get_const(uint32_t idx)
        {
            return constants[idx];
        }
        
        const Value& get_const(uint32_t idx) const
        {
            return constants[idx];
        }
    };

    struct CodeBlock
    {
        std::vector<Instr> instructions;
        std::vector<std::vector<uint8_t>> blobs; // 超长操作数
        std::vector<Value> constants;
        std::vector<std::string> varNames;
        std::vector<std::string> funcNames;
        std::string sourceFile;
        std::unordered_map<std::string, uint32_t> var_name_to_index;
        std::unordered_map<std::string, uint32_t> const_name_to_index;

        void clear()
        {
            instructions.clear();
            blobs.clear();
            constants.clear();
            varNames.clear();
            funcNames.clear();
            sourceFile.clear();
            var_name_to_index.clear();
            const_name_to_index.clear();
        }

        bool isValid() const
        {
            return !instructions.empty();
        }
        
        void build_name_maps()
        {
            var_name_to_index.clear();
            for (size_t i = 0; i < varNames.size(); i++)
            {
                var_name_to_index[varNames[i]] = static_cast<uint32_t>(i);
            }
            const_name_to_index.clear();
            for (size_t i = 0; i < funcNames.size(); i++)
            {
                const_name_to_index[funcNames[i]] = static_cast<uint32_t>(i);
            }
        }
        
        uint32_t get_var_index(const std::string& name) const
        {
            auto it = var_name_to_index.find(name);
            return (it != var_name_to_index.end()) ? it->second : static_cast<uint32_t>(-1);
        }
        
        uint32_t get_const_index(const std::string& name) const
        {
            auto it = const_name_to_index.find(name);
            return (it != const_name_to_index.end()) ? it->second : static_cast<uint32_t>(-1);
        }
    };
    
    

    inline void Value::destroy()
    {
        switch (m_type)
        {
        case Type::STRING:
            delete static_cast<Box<std::string> *>(m_obj);
            break;
        case Type::ARRAY:
            delete static_cast<Box<std::vector<Value>> *>(m_obj);
            break;
        case Type::DICT:
            delete static_cast<Box<std::unordered_map<std::string, Value>> *>(m_obj);
            break;
        case Type::FUNCTION:
            delete static_cast<Box<FunctionType> *>(m_obj);
            break;
        case Type::CODEBLOCK:
            delete static_cast<Box<CodeBlock> *>(m_obj);
            break;
        default:
            break;
        }
    }

    inline Value::Value(const char *v) : m_type(Type::STRING), m_bits(0)
    {
        m_obj = new Box<std::string>(v);
    }
    inline Value::Value(const std::string &v) : m_type(Type::STRING), m_bits(0)
    {
        m_obj = new Box<std::string>(v);
    }
    inline Value::Value(std::string &&v) : m_type(Type::STRING), m_bits(0)
    {
        m_obj = new Box<std::string>(std::move(v));
    }
    inline Value::Value(const std::vector<Value> &v) : m_type(Type::ARRAY), m_bits(0)
    {
        m_obj = new Box<std::vector<Value>>(v);
    }
    inline Value::Value(ArrayPtr ptr) : m_type(Type::ARRAY), m_bits(0)
    {
        if (!ptr)
        {
            m_type = Type::NULL_;
            return;
        }
        m_obj = ptr.box();
        ++m_obj->rc;
    }
    inline Value::Value(const std::unordered_map<std::string, Value> &v) : m_type(Type::DICT), m_bits(0)
    {
        m_obj = new Box<std::unordered_map<std::string, Value>>(v);
    }
    inline Value::Value(DictPtr ptr) : m_type(Type::DICT), m_bits(0)
    {
        if (!ptr)
        {
            m_type = Type::NULL_;
            return;
        }
        m_obj = ptr.box();
        ++m_obj->rc;
    }
    inline Value::Value(const FunctionType &v) : m_type(Type::FUNCTION), m_bits(0)
    {
        m_obj = new Box<FunctionType>(v);
    }
    inline Value::Value(CodeBlockPtr ptr) : m_type(Type::CODEBLOCK), m_bits(0)
    {
        if (!ptr)
        {
            m_type = Type::NULL_;
            return;
        }
        m_obj = ptr.box();
        ++m_obj->rc;
    }
    inline Value::Value(const CodeBlock &v) : m_type(Type::CODEBLOCK), m_bits(0)
    {
        m_obj = new Box<CodeBlock>(v);
    }

    inline Value Value::createArray() { return Value(ArrayPtr::make()); }
    inline Value Value::createDict() { return Value(DictPtr::make()); }

    inline const std::string &Value::asString() const
    {
        if (m_type != Type::STRING)
            throw std::runtime_error("not a string");
        return static_cast<Box<std::string> *>(m_obj)->obj;
    }
    inline std::string &Value::asString()
    {
        if (m_type != Type::STRING)
            throw std::runtime_error("not a string");
        return static_cast<Box<std::string> *>(m_obj)->obj;
    }
    inline std::vector<Value> &Value::asArray()
    {
        if (m_type != Type::ARRAY)
            throw std::runtime_error("not an array");
        return static_cast<Box<std::vector<Value>> *>(m_obj)->obj;
    }
    inline const std::vector<Value> &Value::asArray() const
    {
        if (m_type != Type::ARRAY)
            throw std::runtime_error("not an array");
        return static_cast<Box<std::vector<Value>> *>(m_obj)->obj;
    }
    inline std::unordered_map<std::string, Value> &Value::asDict()
    {
        if (m_type != Type::DICT)
            throw std::runtime_error("not a dict");
        return static_cast<Box<std::unordered_map<std::string, Value>> *>(m_obj)->obj;
    }
    inline const std::unordered_map<std::string, Value> &Value::asDict() const
    {
        if (m_type != Type::DICT)
            throw std::runtime_error("not a dict");
        return static_cast<Box<std::unordered_map<std::string, Value>> *>(m_obj)->obj;
    }
    inline const FunctionType &Value::asFunction() const
    {
        if (m_type != Type::FUNCTION)
            throw std::runtime_error("not a function");
        return static_cast<Box<FunctionType> *>(m_obj)->obj;
    }
    inline CodeBlockPtr Value::asCodeBlock() const
    {
        if (m_type != Type::CODEBLOCK)
            throw std::runtime_error("not a codeblock");
        auto *b = static_cast<Box<CodeBlock> *>(m_obj);
        ++b->rc;
        return CodeBlockPtr(b);
    }
    inline const void *Value::getPtr() const
    {
        if (m_type == Type::ARRAY || m_type == Type::DICT || m_type == Type::CODEBLOCK)
            return m_obj;
        return nullptr;
    }

    // 只处理堆类型
    inline bool Value::isTruthySlow() const
    {
        switch (m_type)
        {
        case Type::STRING:
            return !asString().empty();
        case Type::ARRAY:
            return !asArray().empty();
        case Type::DICT:
            return !asDict().empty();
        default:
            return true;
        }
    }

    inline std::string Value::toString() const
    {
        switch (m_type)
        {
        case Type::NONE:
            return "<none>";
        case Type::NULL_:
            return "null";
        case Type::BOOL:
            return m_bool ? "true" : "false";
        case Type::NUMBER:
            return std::to_string(m_num);
        case Type::STRING:
            return asString();
        case Type::ARRAY:
            return "[...]";
        case Type::DICT:
            return "{...}";
        case Type::FUNCTION:
            return "<function>";
        case Type::BUILTIN:
            return "<builtin>";
        case Type::CODEBLOCK:
            return "<codeblock>";
        default:
            return "unknown";
        }
    }

}

#endif