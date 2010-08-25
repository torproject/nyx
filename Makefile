PYTHON=`which python`
DESTDIR=/
BUILDIR=$(CURDIR)/debian/arm
PROJECT=arm
MANPAGE=arm.1

all:
	@echo "Nothing to see here"

source:
	$(PYTHON) setup.py sdist $(COMPILE)

install:
	$(PYTHON) setup.py install --root $(DESTDIR) $(COMPILE)
	test -d $(DESTDIR)/usr/share/man/man1/ || mkdir -p $(DESTDIR)/usr/share/man/man1/
	cp $(MANPAGE) $(DESTDIR)/usr/share/man/man1/


deb:
	dpkg-buildpackage -rfakeroot -us -uc -I.svn -i.svn -I.pyc -i.pyc

deb-src:
	dpkg-buildpackage -S -rfakeroot -us -uc -I.svn -i.svn -I.pyc -i.pyc

deb-clean:
	-rm -r build
	debian/rules clean

clean:
	fakeroot $(PYTHON) setup.py clean
	fakeroot $(MAKE) -f $(CURDIR)/debian/rules clean
	rm -rf build/ MANIFEST
	find . -name '*.pyc' -delete
