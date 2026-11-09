#include <errno.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
int main(void){errno=0;long p=syscall(66);int e=p<0?errno:0;if(p==0)_exit(0);if(p>0)waitpid((pid_t)p,0,0);printf("direct66_errno=%d\n",e);return 0;}
