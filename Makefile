PYTHON=`which python`
DESTDIR=/
BUILDIR=$(CURDIR)/debian/arm
PROJECT=arm

all:
	@echo "Nothing to see here"

source:
	$(PYTHON) setup.py sdist $(COMPILE)

install:
	$(PYTHON) setup.py install --root $(DESTDIR) $(COMPILE)

deb:
	dpkg-buildpackage -rfakeroot -us -uc -I.svn -i.svn -I.pyc -i.pyc

deb-src:
	dpkg-buildpackage -S -rfakeroot -us -uc -I.svn -i.svn -I.pyc -i.pyc

deb-clean:
	-rm build
	debian/rules clean

clean:
	fakeroot $(PYTHON) setup.py clean
	fakeroot $(MAKE) -f $(CURDIR)/debian/rules clean
	rm -rf build/ MANIFEST
	find . -name '*.pyc' -delete
