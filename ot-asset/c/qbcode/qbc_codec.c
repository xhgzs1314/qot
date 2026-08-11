#ifdef _WIN32
    #define EXPORT __declspec(dllexport)
#else
    #define EXPORT __attribute__((visibility("default")))
#endif

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static const uint8_t CHAR_MAP[256] = {
    0,
    ['a']=0x01,['b']=0x02,['c']=0x03,['d']=0x04,['e']=0x05,
    ['f']=0x06,['g']=0x07,['h']=0x08,['i']=0x09,['j']=0x0A,
    ['k']=0x0B,['l']=0x0C,['m']=0x0D,['n']=0x0E,['o']=0x0F,
    ['p']=0x10,['q']=0x11,['r']=0x12,['s']=0x13,['t']=0x14,
    ['u']=0x15,['v']=0x16,['w']=0x17,['x']=0x18,['y']=0x19,
    ['z']=0x1A,
    ['A']=0x1B,['B']=0x1C,['C']=0x1D,['D']=0x1E,['E']=0x1F,
    ['F']=0x20,['G']=0x21,['H']=0x22,['I']=0x23,['J']=0x24,
    ['K']=0x25,['L']=0x26,['M']=0x27,['N']=0x28,['O']=0x29,
    ['P']=0x2A,['Q']=0x2B,['R']=0x2C,['S']=0x2D,['T']=0x2E,
    ['U']=0x2F,['V']=0x30,['W']=0x31,['X']=0x32,['Y']=0x33,
    ['Z']=0x34,
    ['0']=0x35,['1']=0x36,['2']=0x37,['3']=0x38,['4']=0x39,
    ['5']=0x3A,['6']=0x3B,['7']=0x3C,['8']=0x3D,['9']=0x3E,
    [' ']=0x50,['\n']=0x51,['\t']=0x52,['\r']=0x53,
    ['=']=0x54,['+']=0x55,['-']=0x56,['*']=0x57,['/']=0x58,
    ['(']=0x59,[')']=0x5A,['{']=0x5B,['}']=0x5C,['[']=0x5D,
    [']']=0x5E,[';']=0x5F,[',']=0x60,['.']=0x61,[':']=0x62,
    ['"']=0x63,['\'']=0x64,
    ['&']=0x66,['|']=0x67,['!']=0x68,['?']=0x69,
    ['<']=0x6A,['>']=0x6B,['_']=0x6C,['@']=0x6D,
    ['#']=0x6E,['$']=0x6F,['%']=0x70,['^']=0x71,
    ['~']=0x72,['\\']=0x73,
};
static const char REV_MAP[256] = {
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    'a','b','c','d','e','f','g','h','i','j','k','l','m',
    'n','o','p','q','r','s','t','u','v','w','x','y','z',
    'A','B','C','D','E','F','G','H','I','J','K','L','M',
    'N','O','P','Q','R','S','T','U','V','W','X','Y','Z',
    '0','1','2','3','4','5','6','7','8','9',
    ' ','\n','\t','\r',
    '=','+','-','*','/','(',')','{','}','[',']',';',',','.',':','"','\'',
    '&','|','!','?','<','>','_','@','#','$','%','^','~','\\',
};

EXPORT uint8_t* qbc_encode(const char* input, size_t input_len, size_t* output_len) {
    uint8_t* output = (uint8_t*)malloc(input_len * 2 + 4);
    if (!output) return NULL;
    size_t pos = 0;
    output[pos++] = 'Q';
    output[pos++] = 'B';
    output[pos++] = 'C';
    output[pos++] = 0x01;
    
    for (size_t i = 0; i < input_len; i++) {
        uint8_t ch = (uint8_t)input[i];
        if (ch < 256 && CHAR_MAP[ch] != 0) {
            output[pos++] = CHAR_MAP[ch];
        } else {
            output[pos++] = 0xFF;
            output[pos++] = ch;
        }
    }
    
    *output_len = pos;
    return output;
}

EXPORT char* qbc_decode(const uint8_t* input, size_t input_len, size_t* output_len) {
    if (input_len < 4 || input[0] != 'Q' || input[1] != 'B' || input[2] != 'C') {
        return NULL;
    }
    
    char* output = (char*)malloc(input_len + 1);
    if (!output) return NULL;
    
    size_t pos = 0;
    size_t i = 4;
    
    while (i < input_len) {
        uint8_t code = input[i];
        if (code == 0xFF) {
            i++;
            if (i < input_len) {
                output[pos++] = (char)input[i];
            }
        } else if (code < 256 && REV_MAP[code] != 0) {
            output[pos++] = REV_MAP[code];
        } else {
            output[pos++] = (char)code;
        }
        i++;
    }
    
    output[pos] = '\0';
    *output_len = pos;
    return output;
}

EXPORT void qbc_free(void* ptr) {
    if (ptr) free(ptr);
}