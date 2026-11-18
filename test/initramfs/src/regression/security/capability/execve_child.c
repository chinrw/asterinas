// SPDX-License-Identifier: MPL-2.0

#include <stdint.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <linux/capability.h>

#include "../../common/test.h"

static uint64_t effective;
static uint64_t permitted;
static uint64_t inheritable;

FN_SETUP(parse_cmdline)
{
	FILE *fp;
	int first;

	fp = CHECK_WITH(fopen("/proc/self/cmdline", "r"), _ret != NULL);

	while (CHECK_WITH(fgetc(fp), _ret != EOF) != '\0')
		;

	first = CHECK_WITH(fgetc(fp), _ret != EOF);
	CHECK(ungetc(first, fp));
	if (first == '-') {
		const char script_mode[] = "--script";
		char argument[sizeof(script_mode)];

		CHECK_WITH(fread(argument, 1, sizeof(argument), fp),
			   _ret == sizeof(argument));
		CHECK_WITH(memcmp(argument, script_mode, sizeof(script_mode)),
			   _ret == 0);
		// The kernel inserts the script pathname after the interpreter argument.
		while (CHECK_WITH(fgetc(fp), _ret != EOF) != '\0')
			;
	}

	CHECK_WITH(fscanf(fp, "%lx\n", &effective), _ret == 1);
	CHECK_WITH(fgetc(fp), _ret == '\0');
	CHECK_WITH(fscanf(fp, "%lx\n", &permitted), _ret == 1);
	CHECK_WITH(fgetc(fp), _ret == '\0');
	CHECK_WITH(fscanf(fp, "%lx\n", &inheritable), _ret == 1);
	CHECK_WITH(fgetc(fp), _ret == '\0');

	CHECK(fclose(fp));
}
END_SETUP()

FN_TEST(check_caps)
{
	struct __user_cap_header_struct hdr;
	struct __user_cap_data_struct data[2];

	hdr.version = _LINUX_CAPABILITY_VERSION_3;
	hdr.pid = 0;

	TEST_SUCC(syscall(SYS_capget, &hdr, data));

	TEST_RES(data[0].effective | (((uint64_t)data[1].effective) << 32),
		 _ret == effective);
	TEST_RES(data[0].permitted | (((uint64_t)data[1].permitted) << 32),
		 _ret == permitted);
	TEST_RES(data[0].inheritable | (((uint64_t)data[1].inheritable) << 32),
		 _ret == inheritable);
}
END_TEST()
