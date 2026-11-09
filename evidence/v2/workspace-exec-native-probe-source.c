#include <errno.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
int main(void) {
 errno=0;pid_t a=fork();if(a==0)_exit(0);int fork_error=a<0?errno:0;if(a>0)waitpid(a,0,0);
 errno=0;pid_t b=vfork();if(b==0)_exit(0);int vfork_error=b<0?errno:0;if(b>0)waitpid(b,0,0);
 printf("fork_errno=%d vfork_errno=%d\n",fork_error,vfork_error);return 0;
}
