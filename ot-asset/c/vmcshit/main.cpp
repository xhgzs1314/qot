/***
 *    ____                _           _   ____           ___   ___ _____      ____          ___        __       __  ___   _
 *   / ___|_ __ ___  __ _| |_ ___  __| | | __ ) _   _   / _ \ / _ |_   _|    / ___|___   __| \ \      / ___     \ \/ | | | |
 *  | |   | '__/ _ \/ _` | __/ _ \/ _` | |  _ \| | | | | | | | | | || |_____| |   / _ \ / _` |\ \ /\ / / _ \_____\  /| |_| |
 *  | |___| | |  __| (_| | ||  __| (_| | | |_) | |_| | | |_| | |_| || |_____| |__| (_) | (_| | \ V  V |  __|_____/  \|  _  |
 *   \____|_|  \___|\__,_|\__\___|\__,_| |____/ \__, |  \__\_\\___/ |_|      \____\___/ \__,_|  \_/\_/ \___|    /_/\_|_| |_|
 *                                              |___/
 */
#include "vm_executor.h"
#include <iostream>
#include <string>
#include <windows.h>
void printUsage(const char *progName)
{
    std::cout << "QOT" << std::endl;
    std::cout << "Usage: " << progName << " <bytecode.qtc>" << std::endl;
    std::cout << std::endl;
    std::cout << "Example:" << std::endl;
    std::cout << "  " << progName << " script.qtc" << std::endl;
}

int main(int argc, char *argv[])
{
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);
#endif
    if (argc < 2)
    {
        printUsage(argv[0]);
        return 1;
    }

    std::string filename = argv[1];

    if (filename.size() < 4 || filename.substr(filename.size() - 4) != ".qtc")
    {
        std::cerr << "[Error] 执行文件后缀必须为.qtc" << std::endl;
        return 1;
    }

    qot::VMExecutor vm;

    if (vm.executeFile(filename))
    {
        qot::Value result = vm.getReturnValue();
        if (!result.isNull())
        {
            std::cout << "程序返回值:" << result.toString() << std::endl;
        }
        return 0;
    }
    else
    {
        std::cerr << "程序执行失败" << std::endl;
        return 1;
    }
}